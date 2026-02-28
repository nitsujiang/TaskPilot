import sys

sys.path.append("..")
from agent.agent import process_message

# Test 1: Vague message (should trigger clarifying question)
print("=== TEST 1: Vague Message ===")
process_message(
    "Hey @TaskPilot, someone needs to order more coffee for the office soon"
)

print("\n")

# Test 2: Detailed message (should extract cleanly)
print("=== TEST 2: Detailed Message ===")
process_message(
    "Hey @TaskPilot, Sarah needs to submit the budget report by Wednesday April 3rd"
)

print("\n")

# Test 3: Missing owner (should ask who is responsible)
print("=== TEST 3: Missing Owner ===")
process_message(
    "Hey @TaskPilot, the new employee onboarding guide needs to be updated by next Friday"
)

print("\n")

# Test 4: Complete details (should need no clarification)
print("=== TEST 4: Complete Message ===")
process_message(
    "Hey @TaskPilot, Alex needs to finalize the marketing campaign by April 10th, it's high priority and currently in progress"
)

