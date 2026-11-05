"""Deterministic input-size treatments; never rewrite textbook algorithms or goals."""
from copy import deepcopy


def textbook_inputs_at_size(name, example, size):
    if type(size) is not int or size < 1:
        raise ValueError("Textbook input sizes must be positive integers.")
    inputs = deepcopy(example["sample_inputs"])
    match name:
        case "Linear Search":
            target = inputs["x"]
            others = [value for value in inputs["A"] if value != target]
            inputs["A"] = [target] + [others[k % len(others)] for k in range(size - 1)]
        case "Finding the Maximal Element":
            original = inputs["A"]
            inputs["A"] = [original[k % len(original)] for k in range(size)]
        case "Binary Search":
            inputs["A"] = [2*k + 1 for k in range(size)]
        case "Pattern Matching":
            original = inputs["A"]
            inputs["A"] = [original[k % len(original)] for k in range(size)]
            inputs["M"] = [0] * size
        case "Exponentiation":
            inputs["n"] = size
        case _:
            raise ValueError(f"No textbook input-size treatment for {name}.")
    return inputs
