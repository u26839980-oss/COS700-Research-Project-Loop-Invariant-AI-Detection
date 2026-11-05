"""Source-attributed benchmark data; no UI or runtime dependencies."""

EXAMPLES = {
    "Linear Search": {
        "source": "Kourie & Watson, Chapter 3.1, Linear Search, pages 56-60.",
        "source_status": "Extracted from the provided book photos, then converted to the prototype's normal Python-style notation.",
        "book_benchmark": True,
        "benchmark_complete": True,
        "book_precondition": "x in A",
        "book_postcondition": "A[i] == x",
        "book_invariant": "x not in A[i + 1:len(A)]",
        "loop_head_bounds": "0 <= i < len(A)",
        "input_domain_notes": "A is a finite integer sequence containing x, so it is nonempty. Repeated values are allowed. A matching index need not be unique.",
        "proof_scope": "Read-only integer arrays; partial correctness. The book carries x in A globally (page 56). Index bounds are explicit domain scaffolding, checked jointly with each candidate.",
        "normal_program": """i = len(A) - 1
while A[i] != x:
    i = i - 1""",
        "program": """i = len(A) - 1
while A[i] != x:
    i = i - 1""",
        "precondition": "x in A",
        "invariant": "0 <= i < len(A) and x in A and all(A[k] != x for k in range(i + 1, len(A)))",
        "postcondition": "A[i] == x",
        "sample_inputs": {"A": [4, 2, 7, 2, 9], "x": 2},
        "source_photo": "book_photos/crops/20260827_185745_R.jpg",
        "conversion_note": "The verification reference adds explicit index bounds and the unchanged input assumption to the book's suffix predicate. This is strengthening for defined array access, not a verbatim book formula.",
        "pattern": "Linear search.",
        "reasoning_hint": "The invariant records the checked suffix: every position to the right of i has already been ruled out.",
    },
    "Finding the Maximal Element": {
        "source": "Kourie & Watson, Chapter 3.2, Finding the Maximal Element, pages 60-66.",
        "source_status": "Extracted from the provided book photos, then converted to the prototype's normal Python-style notation.",
        "book_benchmark": True,
        "benchmark_complete": True,
        "book_precondition": "len(A) > 0",
        "book_postcondition": "A[i] == max(A)",
        "book_invariant": "A[i] == max(A[0:j]) and 0 <= i < j",
        "loop_head_bounds": "0 <= i < j <= len(A)",
        "input_domain_notes": "A is a nonempty finite integer sequence. Values may be negative or repeated; the maximum need not be unique.",
        "proof_scope": "Read-only integer arrays; partial correctness. The stored verification reference strengthens the book's prefix-maximum predicate with j <= len(A).",
        "normal_program": """i = 0
j = 1
while j != len(A):
    if A[j] > A[i]:
        i = j
    j = j + 1""",
        "program": """i = 0
j = 1
while j != len(A):
    if A[j] > A[i]:
        i = j
    j = j + 1""",
        "precondition": "len(A) > 0",
        "invariant": "A[i] == max(A[0:j]) and 0 <= i < j <= len(A)",
        "postcondition": "A[i] == max(A)",
        "sample_inputs": {"A": [3, 8, 2, 8, 1]},
        "source_photo": "book_photos/crops/20260827_185817_L.jpg",
        "conversion_note": "The final loop is visible on page 66. max(A[0:j]) denotes the maximum of the nonempty processed prefix.",
        "pattern": "Finding the maximal element.",
        "reasoning_hint": "The invariant combines a prefix summary with bounds: i is the max index for the processed prefix A[0:j].",
    },
    "Binary Search": {
        "source": "Kourie & Watson, Chapter 3.3, Binary Search, pages 66-73.",
        "source_status": "Extracted from the provided book photos, then converted to the prototype's normal Python-style notation.",
        "book_benchmark": True,
        "benchmark_complete": True,
        "book_precondition": "A is sorted",
        "book_postcondition": "(x in A and A[i] == x) or (x not in A and i == -1)",
        "book_invariant": "A is sorted and (x in A implies x in A[l:h])",
        "loop_head_bounds": "0 <= l <= h <= len(A)",
        "input_domain_notes": "A is sorted in nondecreasing order, not necessarily strictly increasing. Duplicates are allowed. A may be empty and x may be absent. The result -1 denotes absence, not a valid element index.",
        "proof_scope": "Read-only sorted integer arrays, including empty arrays and absent targets; partial correctness. Midpoint uses floor division. Interval bounds are checked jointly with the candidate.",
        "normal_program": """l = 0
h = len(A)
while h > l + 1:
    m = (l + h) // 2
    if x < A[m]:
        h = m
    elif x == A[m]:
        l = m
        h = m + 1
    else:
        l = m

if h == l + 1:
    if A[l] == x:
        i = l
    else:
        i = -1
else:
    i = -1""",
        "program": """l = 0
h = len(A)
while h > l + 1:
    m = (l + h) // 2
    if x < A[m]:
        h = m
    elif x == A[m]:
        l = m
        h = m + 1
    else:
        l = m

if h == l + 1:
    if A[l] == x:
        i = l
    else:
        i = -1
else:
    i = -1""",
        "precondition": "all(all(A[j] <= A[k] for k in range(j, len(A))) for j in range(len(A)))",
        "invariant": "0 <= l <= h <= len(A) and all(all(A[j] <= A[k] for k in range(j, len(A))) for j in range(len(A))) and (x not in A or any(A[k] == x for k in range(l, h)))",
        "postcondition": "(x in A and A[i] == x) or (x not in A and i == -1)",
        "sample_inputs": {"A": [1, 3, 5, 7, 9], "x": 7},
        "source_photo": "book_photos/crops/20260827_185839_R.jpg",
        "conversion_note": "The final loop and post-loop result assignment are visible. Sortedness and implication are translated to executable predicates; index bounds are explicit verification strengthening.",
        "pattern": "Binary search.",
        "reasoning_hint": "The invariant keeps the sortedness assumption and says the current search window still contains x if x exists.",
    },
    "Pattern Matching": {
        "source": "Kourie & Watson, Chapter 3.4, Pattern Matching, pages 74-77.",
        "source_status": "The specification and final nested-loop program are visible in the provided photos. The Python conversion retains the inner loop; its outer bound follows the stated specification, with the discrepancy in the printed final listing documented below.",
        "book_benchmark": True,
        "benchmark_complete": True,
        "book_precondition": "len(M) >= len(A) and len(p) > 0",
        "book_postcondition": "for every k in range(0, len(A) - len(p) + 1), M[k] records whether A[k:k + len(p)] == p",
        "book_invariant": "for every k in range(0, i), M[k] == 1 if A[k:k + len(p)] == p else M[k] == -1",
        "loop_head_bounds": "0 <= i <= len(A)",
        "input_domain_notes": "p is nonempty. A may be empty or shorter than p; do not assume len(A) >= len(p). M has at least len(A) entries. No other relation between the input lengths is assumed.",
        "proof_scope": "Integer-sequence adaptation of the book's character arrays. Nonempty pattern is required on page 74. The checker infers a bounded unit-step scan annotation from the actual inner loop and checks its initialization, safety, preservation and exit. Element writes use symbolic Store; array lengths are preserved. This proves partial correctness, not termination.",
        "normal_program": """i = 0
while i < len(A) - len(p) + 1:
    j = 0
    while j != len(p) and p[j] == A[i + j]:
        j = j + 1
    if j == len(p):
        M[i] = 1
    else:
        M[i] = -1
    i = i + 1""",
        "program": """i = 0
while i < len(A) - len(p) + 1:
    j = 0
    while j != len(p) and p[j] == A[i + j]:
        j = j + 1
    if j == len(p):
        M[i] = 1
    else:
        M[i] = -1
    i = i + 1""",
        "precondition": "len(M) >= len(A) and len(p) > 0",
        "invariant": "0 <= i and all((M[k] == 1 and A[k:k + len(p)] == p) or (M[k] == -1 and A[k:k + len(p)] != p) for k in range(i))",
        "postcondition": "all((M[k] == 1 and A[k:k + len(p)] == p) or (M[k] == -1 and A[k:k + len(p)] != p) for k in range(len(A) - len(p) + 1))",
        "sample_inputs": {"A": [1, 2, 1, 2, 3], "p": [1, 2], "M": [0, 0, 0, 0, 0]},
        "source_photo": "book_photos/crops/20260827_185853_R.jpg",
        "conversion_note": "Adapted, not verbatim: the final photographed listing uses i < len(A)-len(p), whereas the specification includes the last start index. This conversion uses i < len(A)-len(p)+1 to satisfy that specification. The nonempty-pattern assumption is explicit. The bounded inner scan and element writes are checked symbolically.",
        "pattern": "Pattern matching.",
        "reasoning_hint": "The invariant describes the processed prefix: all positions before i have already been classified correctly.",
    },
    "Exponentiation": {
        "source": "Kourie & Watson, Chapter 3.5, Exponentiation, pages 77-83.",
        "source_status": "Extracted from the provided book photos, then converted to the prototype's normal Python-style notation.",
        "book_benchmark": True,
        "benchmark_complete": True,
        "book_precondition": "n > 0",
        "book_postcondition": "z == a ** n",
        "book_invariant": "z * (b ** i) == a ** n",
        "loop_head_bounds": "i >= 0",
        "input_domain_notes": "n is a positive integer. This prototype uses integer a, including zero and negative values; it does not implement the book's full real-valued input domain.",
        "proof_scope": "Integer-base specialization of the real-base textbook problem, with positive integer n. Natural powers use sound ground identities, including a square law with Z3-checked base/induction steps. Exponents must be nonnegative; 0**0 is 1. Inconclusive power abstractions are not called counterexamples. This proves partial correctness, not termination or a complete power decision procedure.",
        "normal_program": """i = n - 1
b = a
z = a
while i != 0:
    if i % 2 == 1:
        i = i - 1
        z = z * b
    else:
        i = i // 2
        b = b * b""",
        "program": """i = n - 1
b = a
z = a
while i != 0:
    if i % 2 == 1:
        i = i - 1
        z = z * b
    else:
        i = i // 2
        b = b * b""",
        "precondition": "n > 0",
        "invariant": "z * (b ** i) == a ** n",
        "postcondition": "z == a ** n",
        "sample_inputs": {"a": 2, "n": 5},
        "source_photo": "book_photos/crops/20260827_185929_L.jpg",
        "conversion_note": "Final algorithm visible on page 82. Integer bases specialize the book's real-valued base. Symbolic nonnegative integer powers are supported through sound ground identities, without bounded unrolling.",
        "pattern": "Exponentiation.",
        "reasoning_hint": "The invariant preserves the mathematical target while work moves between z, b, and i.",
    },
    "Countdown Transfer": {
        "source": "Local scalar benchmark derived from the standard transfer/counter loop pattern.",
        "source_status": "Purpose-built for the prototype because it has a meaningful invariant and is fully supported by the current scalar Z3 checker.",
        "benchmark_complete": True,
        "program": """n = 5
x = n
y = 0
while x > 0:
    x = x - 1
    y = y + 1""",
        "precondition": "n >= 0",
        "invariant": "x + y == n and x >= 0",
        "postcondition": "y == n",
        "pattern": "Transfer loop where work moves from x into y while preserving the total.",
        "reasoning_hint": "The invariant captures conservation: each iteration decreases x and increases y by the same amount, so x + y remains n.",
        "trace_iteration_limit": 25,
    },
}
