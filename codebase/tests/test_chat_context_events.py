import unittest
from api.llm_service import LLMService


class TimelinePromptTests(unittest.TestCase):
    def test_context_events_are_not_sent_to_the_answer_model(self):
        history = [{'role':'user','content':'Previous question'},
                   {'role':'assistant','content':'Previous answer'}]
        history += [{'role':'event','content':f'Document removed: private-event-{i}'} for i in range(8)]
        prompt = LLMService()._build_prompt('Current question','Synthetic context',history)
        self.assertIn('Previous question',prompt)
        self.assertIn('Previous answer',prompt)
        self.assertNotIn('private-event-',prompt)
        self.assertIn('Current question',prompt)
