"""FSM States for PostBot"""
from aiogram.fsm.state import State, StatesGroup


class PostStates(StatesGroup):
    # Post creation
    content = State()
    media = State()
    time = State()
    url_btn = State()
    config = State()
    
    # Post editing
    edit_content = State()
    edit_media = State()
    edit_time = State()
    edit_url = State()
    add_media = State()
    
    # Reaction buttons
    add_reaction = State()
    
    # Templates
    template_name = State()
    template_content = State()
    
    # Import
    import_file = State()
    
    # Multi-chat selection
    selecting_chats = State()


# Alias for backwards compatibility
S = PostStates
