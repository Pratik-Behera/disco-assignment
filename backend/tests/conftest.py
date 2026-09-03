import os

# Keep API tests off the live model even when backend/.env has a key.
os.environ["DISCO_FORCE_HEURISTIC"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
