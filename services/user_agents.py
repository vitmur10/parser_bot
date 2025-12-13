import random
from config import USER_AGENTS

def get_random_ua() -> str:
    return random.choice(USER_AGENTS)