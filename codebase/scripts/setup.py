#!/usr/bin/env python3
"""
scripts/setup.py  —  One-click setup for Enterprise Knowledge Copilot Phase 1

Run: python scripts/setup.py

Does:
  1. Creates Python virtual environment
  2. Installs requirements
  3. Downloads spaCy model
  4. Starts Qdrant via Docker
  5. Starts PostgreSQL via Docker
  6. Starts Redis via Docker
  7. Creates database schema
  8. Pulls Ollama model
  9. Prints startup instructions
"""
import os
import platform
import subprocess
import sys
import time


def run(cmd: str, check: bool = True, shell: bool = True) -> int:
    print(f"  $ {cmd}")
    result = subprocess.run(cmd, shell=shell, check=check)
    return result.returncode


def check_command(cmd: str) -> bool:
    try:
        subprocess.run(cmd, shell=True, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False


IS_WINDOWS = platform.system() == "Windows"
PYTHON = sys.executable
VENV = ".venv"
VENV_PYTHON = f"{VENV}\\Scripts\\python.exe" if IS_WINDOWS else f"{VENV}/bin/python"
VENV_PIP = f"{VENV}\\Scripts\\pip.exe" if IS_WINDOWS else f"{VENV}/bin/pip"


def main():
    print("\n" + "="*60)
    print("  Enterprise Knowledge Copilot — Phase 1 Setup")
    print("="*60 + "\n")

    # Check Docker
    print("📋 Checking prerequisites...")
    if not check_command("docker --version"):
        print("❌ Docker not found. Install Docker Desktop from https://docker.com")
        print("   Docker is needed for Qdrant, PostgreSQL, and Redis.")
        sys.exit(1)
    print("  ✅ Docker found")

    if not check_command("ollama --version"):
        print("⚠️  Ollama not found. Install from https://ollama.ai")
        print("   Then run: ollama pull phi3:mini")
        print("   Continuing without Ollama (you'll need it for LLM responses)")
    else:
        print("  ✅ Ollama found")

    # Python venv
    print("\n📦 Setting up Python environment...")
    if not os.path.exists(VENV):
        run(f"{PYTHON} -m venv {VENV}")
        print("  ✅ Virtual environment created")

    # Install requirements
    print("\n📦 Installing Python packages (this takes 3-5 min on first run)...")
    run(f"{VENV_PIP} install --upgrade pip")
    run(f"{VENV_PIP} install -r requirements.txt")
    print("  ✅ Packages installed")

    # spaCy model
    print("\n🔤 Downloading spaCy NLP model (en_core_web_sm)...")
    run(f"{VENV_PYTHON} -m spacy download en_core_web_sm")
    print("  ✅ spaCy model ready")

    # Docker services
    print("\n🐳 Starting Docker services...")

    # Qdrant
    print("  Starting Qdrant (vector database)...")
    run(
        'docker run -d --name qdrant-ekc '
        '-p 6333:6333 '
        '-v qdrant_storage:/qdrant/storage '
        'qdrant/qdrant:latest',
        check=False
    )

    # PostgreSQL
    print("  Starting PostgreSQL...")
    run(
        'docker run -d --name postgres-ekc '
        '-e POSTGRES_PASSWORD=postgres '
        '-e POSTGRES_DB=enterprise_copilot '
        '-p 5432:5432 '
        'postgres:16-alpine',
        check=False
    )

    # Redis
    print("  Starting Redis...")
    run(
        'docker run -d --name redis-ekc '
        '-p 6379:6379 '
        'redis:7-alpine',
        check=False
    )

    print("  ⏳ Waiting 8 seconds for services to initialize...")
    time.sleep(8)

    # Initialize database schema
    print("\n🗄️  Creating database schema...")
    run(
        'docker exec postgres-ekc psql -U postgres -d enterprise_copilot '
        '-f /dev/stdin < ingestion/db_schema.sql',
        check=False,
    )
    # Alternative: direct psql
    run(
        f'{VENV_PYTHON} -c "from scripts.init_db import init_db; init_db()"',
        check=False
    )

    # Ollama model
    if check_command("ollama --version"):
        print("\n🤖 Pulling Ollama model (phi3:mini ~2.3GB)...")
        print("  This may take 5-15 min depending on internet speed...")
        run("ollama pull phi3:mini", check=False)
        print("  ✅ Model ready")

    # Copy env file
    if not os.path.exists("configs/.env"):
        import shutil
        shutil.copy("configs/.env.example", "configs/.env")
        print("\n✅ Created configs/.env (edit password if needed)")

    # Done
    print("\n" + "="*60)
    print("  ✅ Setup complete!")
    print("="*60)
    print("\n🚀 To start the system:\n")
    print("  Terminal 1 — Backend API:")
    if IS_WINDOWS:
        print(f"    {VENV_PYTHON} -m uvicorn api.main:app --reload --port 8000")
    else:
        print(f"    {VENV_PYTHON} -m uvicorn api.main:app --reload --port 8000")

    print("\n  Terminal 2 — Chat UI:")
    if IS_WINDOWS:
        print(f"    {VENV}\\Scripts\\streamlit run ui/app.py")
    else:
        print(f"    {VENV}/bin/streamlit run ui/app.py")

    print("\n  Terminal 3 — Ollama (if not running):")
    print("    ollama serve")

    print("\n📄 First steps after startup:")
    print("  1. Open http://localhost:8501 (Streamlit UI)")
    print("  2. In sidebar, click 'Check Status' — all should be green")
    print("  3. Upload tickets.csv using 'Ingest Tickets CSV'")
    print("  4. Upload your GitLab Handbook PDFs one by one")
    print("  5. Start asking questions!")
    print("\n  API docs: http://localhost:8000/docs")
    print("="*60 + "\n")


if __name__ == "__main__":
    main()
