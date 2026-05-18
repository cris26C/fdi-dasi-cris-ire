from typing import List, Dict

# Active sessions: used by the LLM as conversational context.
# Cleared on _reset_negotiation so each new negotiation starts fresh from the LLM's POV.
active_sessions: Dict[str, List[dict]] = {}

#Full archive of all messages
archive_sessions: Dict[str, List[dict]] = {}


class Memory:
    def __init__(self):
        pass

    def add_message(self, agent_name: str, role: str, content: str):
        entry = {"role": role, "content": content}
        active_sessions.setdefault(agent_name, []).append(entry)
        archive_sessions.setdefault(agent_name, []).append(entry)

    def get_history(self, agent_name: str) -> List[dict]:
        """LLM-facing history: only the current active session."""
        return list(active_sessions.get(agent_name, []))

    def get_all_history(self) -> Dict[str, List[dict]]:
        """Dashboard-facing history: full archive (nothing ever gets dropped)."""
        return archive_sessions

    def agent_name_in_memory(self, agent_name: str) -> bool:
        return agent_name in archive_sessions

    def mark_cycle_boundary(self, agent_name: str):
        """Insert a visual separator into the archive when a negotiation cycle ends."""
        archive_sessions.setdefault(agent_name, []).append({
            "role": "system",
            "content": "[--- fin de ciclo de negociación ---]"
        })