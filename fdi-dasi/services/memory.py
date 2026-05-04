from typing import List, Dict

active_sessions: Dict[str, List[dict]] = {}

class Memory:
    def __init__(self):
        pass

    def add_message(self, agent_name: str, role: str, content: str):
        active_sessions.setdefault(agent_name, [])
        active_sessions[agent_name].append(
            {"role": role, "content": content}
        )

    def get_history(self, agent_name: str) -> List[dict]:
        return list(active_sessions.get(agent_name, []))
    
    def get_all_history(self) -> Dict[str, List[dict]]:
        return active_sessions
    
    def agent_name_in_memory(self, agent_name: str) -> bool:
        return agent_name in active_sessions