"""LangGraph matrix shortcut; invokes actual StateGraph and native interrupt."""
import sys
from .run import main
if __name__ == "__main__":
    if "--framework" not in sys.argv: sys.argv += ["--framework","langgraph"]
    main()
