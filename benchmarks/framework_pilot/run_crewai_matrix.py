"""CrewAI matrix shortcut; native hook-dispatch level, no Crew.kickoff() yet."""
import sys
from .run import main
if __name__ == "__main__":
    if "--framework" not in sys.argv: sys.argv += ["--framework","crewai"]
    main()
