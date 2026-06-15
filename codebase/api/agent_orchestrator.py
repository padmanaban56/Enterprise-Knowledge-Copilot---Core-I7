"""
api/agent_orchestrator.py — Agentic Enhancement (Hackathon Use Case 2, Bonus)

Implements a ReAct-style agent with three tools:
  Tool 1: document_search  — hybrid retrieval over the knowledge base
  Tool 2: ticket_lookup     — ticket dual-path search (exact / SQL / semantic)
  Tool 3: summarizer        — LLM-based summarization of retrieved content

Pattern: ReAct (Reason -> Act -> Observe), bounded to a small number of
iterations. At each step the agent (LLM) emits either:
  - a tool call: {"action": "<tool_name>", "action_input": "<string>"}
  - a final answer: {"action": "final_answer", "action_input": "<answer text>"}

If the LLM is unavailable or returns unparseable output, the orchestrator
falls back to a deterministic Plan-Execute sequence:
  1. document_search(query)
  2. if intent == TICKET_LOOKUP -> ticket_lookup(query)
  3. summarizer(combined results)
  4. final_answer

This guarantees a usable response even without a local LLM running.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from configs.settings import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_ITERATIONS = 4

_AGENT_SYSTEM_PROMPT = """You are an agentic assistant for an Enterprise Knowledge Copilot.
You can use the following tools to answer the user's question:

1. document_search(query): searches internal documentation, SOPs, runbooks and policies. Returns relevant text chunks with citations.
2. ticket_lookup(query): searches the ticket system for matching tickets by ID, keyword, or similar past issues.
3. summarizer(text): summarizes a block of text into a concise answer.

At each step, respond with ONLY a JSON object in one of these forms:
  {"action": "document_search", "action_input": "<search query>"}
  {"action": "ticket_lookup", "action_input": "<search query or ticket id>"}
  {"action": "summarizer", "action_input": "<text to summarize>"}
  {"action": "final_answer", "action_input": "<your final answer to the user, with [Source N] citations if used>"}

Rules:
- Use document_search for "how do I / what is / where is / explain" type questions.
- Use ticket_lookup if the question references a ticket ID or sounds like an incident/issue lookup.
- Use summarizer only after you have retrieved content you need to condense.
- Once you have enough information, respond with final_answer.
- Never call the same tool with the same input twice.
- Respond with ONLY the JSON object, no other text.
"""


@dataclass
class AgentStep:
    step: int
    action: str
    action_input: str
    observation: str = ""


@dataclass
class AgentResult:
    answer: str
    steps: List[AgentStep] = field(default_factory=list)
    citations: List[Dict] = field(default_factory=list)
    pattern: str = "react"   # "react" | "plan_execute_fallback"


class AgentOrchestrator:
    """
    ReAct agent over document_search / ticket_lookup / summarizer, with a
    deterministic Plan-Execute fallback when the LLM is unavailable.
    """

    def __init__(
        self,
        hybrid_engine,
        query_understanding,
        ticket_retriever,
        llm_service,
    ):
        self.hybrid_engine = hybrid_engine
        self.query_understanding = query_understanding
        self.ticket_retriever = ticket_retriever
        self.llm_service = llm_service

    # ══════════════════════════════════════════════════════════════════════
    # TOOLS
    # ══════════════════════════════════════════════════════════════════════
    async def tool_document_search(
        self, query: str, rbac_roles: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Tool 1: Document search via the hybrid retrieval pipeline."""
        query_ctx = self.query_understanding.process(query, rbac_roles=rbac_roles)
        if query_ctx.intent_result and query_ctx.intent_result.low_confidence:
            try:
                query_ctx = await self.query_understanding.classify_intent_llm(query_ctx)
            except Exception:
                pass
        result = await self.hybrid_engine.retrieve_cascading(query_ctx, rbac_roles=rbac_roles)
        built_ctx = self.hybrid_engine.build_context(result.chunks)
        return {
            "context_text": built_ctx.context_text,
            "citations": built_ctx.citations,
            "confidence": result.confidence,
            "low_confidence": result.low_confidence,
        }

    def tool_ticket_lookup(
        self, query: str, ticket_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Tool 2: Ticket lookup via the dual-path ticket retriever."""
        if self.ticket_retriever is None:
            return {"candidates": [], "stats": {"error": "ticket_retriever unavailable"}}

        # Extract a ticket ID from the query if not provided explicitly
        if not ticket_ids:
            ticket_ids = re.findall(r"\b[A-Z]{2,5}-\d{3,8}\b", query.upper())

        path = self.ticket_retriever.dual_path_search(
            query=query,
            ticket_ids=ticket_ids or None,
            filters={},
            vector_store=self.hybrid_engine.vector_store,
        )
        return path

    async def tool_summarizer(self, text: str, max_words: int = 150) -> str:
        """Tool 3: Summarize text via the LLM (Ollama), with a truncation fallback."""
        if not text or not text.strip():
            return ""

        prompt = (
            f"Summarize the following content in at most {max_words} words. "
            f"Preserve any [Source N] citation markers verbatim.\n\n{text}"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 400},
                    },
                )
                data = resp.json()
                summary = data.get("response", "").strip()
                if summary:
                    return summary
        except Exception as e:
            logger.warning(f"Summarizer LLM call failed, falling back to truncation: {e}")

        # Fallback: naive truncation
        words = text.split()
        return " ".join(words[: max_words * 2]) + ("..." if len(words) > max_words * 2 else "")

    # ══════════════════════════════════════════════════════════════════════
    # ReAct LOOP
    # ══════════════════════════════════════════════════════════════════════
    async def run(
        self,
        query: str,
        rbac_roles: Optional[List[str]] = None,
        chat_history: Optional[List[Dict]] = None,
    ) -> AgentResult:
        ollama_ok = await self.llm_service.check_ollama()
        if not ollama_ok:
            return await self._plan_execute_fallback(query, rbac_roles)

        steps: List[AgentStep] = []
        used_tool_inputs: set = set()
        scratchpad = ""

        for i in range(1, MAX_ITERATIONS + 1):
            decision = await self._react_decide(query, scratchpad, chat_history)
            action = decision.get("action", "final_answer")
            action_input = decision.get("action_input", "")

            if action == "final_answer":
                # Collect citations from any document_search steps so far
                citations = []
                for s in steps:
                    if s.action == "document_search":
                        try:
                            obs = json.loads(s.observation)
                            citations.extend(obs.get("citations", []))
                        except Exception:
                            pass
                return AgentResult(answer=action_input, steps=steps, citations=citations, pattern="react")

            # Avoid infinite loops on repeated identical calls
            sig = f"{action}:{action_input}"
            if sig in used_tool_inputs:
                action = "final_answer"
                action_input = "I have enough information to answer based on the steps above."
                continue
            used_tool_inputs.add(sig)

            observation = await self._execute_tool(action, action_input, rbac_roles)
            step = AgentStep(step=i, action=action, action_input=action_input, observation=observation)
            steps.append(step)
            scratchpad += (
                f"\nStep {i}: action={action}, input={action_input}\n"
                f"Observation: {observation[:1500]}\n"
            )

        # Exceeded max iterations -> fall back to Plan-Execute using gathered observations
        return await self._plan_execute_fallback(query, rbac_roles, prior_steps=steps)

    async def _react_decide(
        self, query: str, scratchpad: str, chat_history: Optional[List[Dict]],
    ) -> Dict[str, str]:
        prompt = (
            f"USER QUESTION: {query}\n\n"
            f"{'PREVIOUS STEPS:' + scratchpad if scratchpad else 'No steps taken yet.'}\n\n"
            f"What is your next action?"
        )
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{settings.ollama_base_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "system": _AGENT_SYSTEM_PROMPT,
                        "stream": False,
                        "options": {"temperature": 0.0, "num_predict": 300},
                    },
                )
                data = resp.json()
                text = data.get("response", "").strip()
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    parsed = json.loads(match.group())
                    if "action" in parsed and "action_input" in parsed:
                        return parsed
        except Exception as e:
            logger.warning(f"Agent decision step failed: {e}")

        # If parsing fails, default to a final answer based on whatever we know
        return {"action": "final_answer", "action_input": "I'm unable to process this request right now."}

    async def _execute_tool(self, action: str, action_input: str, rbac_roles: Optional[List[str]]) -> str:
        try:
            if action == "document_search":
                result = await self.tool_document_search(action_input, rbac_roles=rbac_roles)
                return json.dumps(result)
            elif action == "ticket_lookup":
                result = self.tool_ticket_lookup(action_input)
                return json.dumps(result)
            elif action == "summarizer":
                result = await self.tool_summarizer(action_input)
                return result
            else:
                return f"Unknown tool: {action}"
        except Exception as e:
            logger.error(f"Tool execution failed ({action}): {e}")
            return f"Tool error: {e}"

    # ══════════════════════════════════════════════════════════════════════
    # PLAN-EXECUTE FALLBACK (no LLM / agent decision unavailable)
    # ══════════════════════════════════════════════════════════════════════
    async def _plan_execute_fallback(
        self,
        query: str,
        rbac_roles: Optional[List[str]] = None,
        prior_steps: Optional[List[AgentStep]] = None,
    ) -> AgentResult:
        steps: List[AgentStep] = list(prior_steps or [])
        step_n = len(steps) + 1

        # 1. Determine intent to decide whether to include ticket_lookup
        query_ctx = self.query_understanding.process(query, rbac_roles=rbac_roles)

        # 2. document_search
        doc_result = await self.tool_document_search(query, rbac_roles=rbac_roles)
        steps.append(AgentStep(
            step=step_n, action="document_search", action_input=query,
            observation=json.dumps(doc_result),
        ))
        step_n += 1

        # 3. ticket_lookup if intent suggests it
        ticket_result = None
        if query_ctx.intent == "TICKET_LOOKUP":
            ticket_result = self.tool_ticket_lookup(query, ticket_ids=query_ctx.ticket_ids)
            steps.append(AgentStep(
                step=step_n, action="ticket_lookup", action_input=query,
                observation=json.dumps(ticket_result),
            ))
            step_n += 1

        # 4. summarizer over combined context
        combined_text = doc_result.get("context_text", "")
        if ticket_result and ticket_result.get("candidates"):
            ticket_text = "\n".join(
                f"Ticket {t.get('ticket_id')}: {t.get('subject', '')} — {t.get('resolution') or t.get('description', '')}"
                for t in ticket_result["candidates"]
            )
            combined_text = f"{combined_text}\n\n---\nRELATED TICKETS:\n{ticket_text}"

        if doc_result.get("low_confidence") or not combined_text.strip():
            answer = (
                "I couldn't find enough relevant information to answer this confidently. "
                "Could you provide more details or rephrase your question?"
            )
        else:
            answer = await self.tool_summarizer(combined_text, max_words=200)
            steps.append(AgentStep(
                step=step_n, action="summarizer", action_input=combined_text[:200] + "...",
                observation=answer,
            ))

        return AgentResult(
            answer=answer,
            steps=steps,
            citations=doc_result.get("citations", []),
            pattern="plan_execute_fallback",
        )
