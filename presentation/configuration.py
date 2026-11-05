DEFAULT_PROGRAM = """n = 5
x = n
y = 0
while x > 0:
    x = x - 1
    y = y + 1"""

DEFAULT_PRECONDITION = "n >= 0"
DEFAULT_INVARIANT = "x + y == n and x >= 0"
DEFAULT_POSTCONDITION = "y == n"
