

from datetime import datetime

class StateManager:
    def __init__(self):
        self.current_state = {}
        self.INITIAL_STATES = ["pending", "in_progress", "waiting_for_clarification"]
        self.FINAL_STATES = ["completed", "cancelled"]
    
    def add_task(self, task_id, owner, deadline=None):
        """Add new task with default 'pending' state"""
        self.current_state[task_id] = {
            "state": "pending",
            "owner": owner,
            "deadline": deadline,
            "last_update": datetime.now().isoformat(),
            "reminder_count": 0
        }
    
    def get_state(self, task_id):
        """Get current state of a task"""
        return self.current_state[task_id]["state"]
    
    def update_state(self, task_id, new_state):
        """Change state with validation"""
        current = self.get_state(task_id)
        
        if self.is_valid_transition(current, new_state):
            self.current_state[task_id]["state"] = new_state
            self.current_state[task_id]["last_update"] = datetime.now().isoformat()
            return True
        return False
    
    def is_valid_transition(self, current_state, new_state):
        """Check if state transition is allowed"""
        if current_state in self.FINAL_STATES and new_state in self.INITIAL_STATES:
            return False
        return True
    
    def get_tasks_by_state(self, state):
        """Return all task IDs in a given state"""
        return [task_id for task_id, data in self.current_state.items() 
                if data["state"] == state]
    
    def change_owner(self, task_id, new_owner):
        """Transfer ownership"""
        self.current_state[task_id]["owner"] = new_owner
        self.current_state[task_id]["last_update"] = datetime.now().isoformat()
    
    def update_deadline(self, task_id, new_deadline):
        """Update deadline"""
        self.current_state[task_id]["deadline"] = new_deadline
        self.current_state[task_id]["last_update"] = datetime.now().isoformat()