"""Corpus de référence de la capacité CODE REPAIR.

Chaque entrée est une fonction Python réelle, pure et déterministe, avec des
ENTRÉES de test (jamais de sorties : les sorties attendues sont calculées en
exécutant la référence dans le sandbox à la construction du benchmark, puis
restent sur l'hôte). Les bugs ne sont PAS écrits ici : ils sont injectés
mécaniquement (`mutation.py`) et seuls ceux qu'au moins un test détecte sont
retenus.

Catégories :
- `strings`, `lists`, `math`, `search_sort`, `dicts` : familles partagées,
  réparties par fonction entre train / validation / held-out ;
- `parsing` : famille ABSENTE de l'entraînement, uniquement en held-out
  (généralisation à une famille non vue) ;
- `security` : exercices DÉFENSIFS de laboratoire (validation d'entrée,
  chemins, autorisation, bornes, listes blanches), uniquement dans la suite de
  TRANSFERT. Aucun accès à un système réel : ce sont des fonctions pures dont
  les tests contiennent les entrées d'attaque qu'elles doivent refuser.

Écrit pour ce benchmark (aucune copie d'un projet tiers) ; le contrôle de
contamination vérifie en plus qu'aucune solution d'évaluation ne se trouve
dans le corpus d'entraînement."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ReferenceFunction:
    fid: str
    category: str
    source: str
    tests: tuple[tuple[Any, ...], ...]


def _fn(fid: str, category: str, source: str, *tests: tuple[Any, ...]) -> ReferenceFunction:
    return ReferenceFunction(fid, category, source.strip("\n") + "\n", tuple(tests))


STRINGS = [
    _fn(
        "count_vowels",
        "strings",
        """
def count_vowels(text):
    count = 0
    for ch in text.lower():
        if ch in "aeiou":
            count += 1
    return count
""",
        ("hello",),
        ("",),
        ("AEIOU xyz",),
        ("rhythm",),
        ("Banana",),
    ),
    _fn(
        "reverse_words",
        "strings",
        """
def reverse_words(sentence):
    words = sentence.split()
    words.reverse()
    return " ".join(words)
""",
        ("a b c",),
        ("hello world",),
        ("",),
        ("one",),
    ),
    _fn(
        "is_palindrome",
        "strings",
        """
def is_palindrome(text):
    cleaned = [ch.lower() for ch in text if ch.isalnum()]
    return cleaned == cleaned[::-1]
""",
        ("Racecar",),
        ("A man, a plan",),
        ("",),
        ("abca",),
        ("No lemon, no melon",),
    ),
    _fn(
        "capitalize_words",
        "strings",
        """
def capitalize_words(text):
    words = []
    for word in text.split(" "):
        if word:
            words.append(word[0].upper() + word[1:])
        else:
            words.append(word)
    return " ".join(words)
""",
        ("hello world",),
        ("",),
        ("a  b",),
        ("x",),
    ),
    _fn(
        "run_length",
        "strings",
        """
def run_length(text):
    result = []
    i = 0
    while i < len(text):
        j = i
        while j < len(text) and text[j] == text[i]:
            j += 1
        result.append((text[i], j - i))
        i = j
    return result
""",
        ("aaabcc",),
        ("",),
        ("abc",),
        ("zzzz",),
    ),
    _fn(
        "common_prefix",
        "strings",
        """
def common_prefix(words):
    if not words:
        return ""
    prefix = words[0]
    for word in words[1:]:
        while not word.startswith(prefix):
            prefix = prefix[:-1]
    return prefix
""",
        (["flower", "flow", "flight"],),
        (["dog", "car"],),
        ([],),
        (["same", "same"],),
        (["abc"],),
    ),
    _fn(
        "caesar_shift",
        "strings",
        """
def caesar_shift(text, shift):
    result = []
    for ch in text:
        if ch.isalpha() and ch.islower():
            result.append(chr((ord(ch) - ord("a") + shift) % 26 + ord("a")))
        else:
            result.append(ch)
    return "".join(result)
""",
        ("abc", 1),
        ("xyz", 3),
        ("hello, world", 13),
        ("", 5),
        ("aZ", 2),
    ),
    _fn(
        "count_substring",
        "strings",
        """
def count_substring(text, pattern):
    if not pattern:
        return 0
    count = 0
    start = 0
    while True:
        index = text.find(pattern, start)
        if index < 0:
            return count
        count += 1
        start = index + 1
""",
        ("aaaa", "aa"),
        ("abcabc", "abc"),
        ("abc", ""),
        ("abc", "d"),
        ("", "a"),
    ),
    _fn(
        "title_case",
        "strings",
        """
def title_case(text, minor):
    words = text.lower().split()
    result = []
    for i, word in enumerate(words):
        if i > 0 and word in minor:
            result.append(word)
        else:
            result.append(word.capitalize())
    return " ".join(result)
""",
        ("the lord of the rings", ["of", "the"]),
        ("A tale", ["a"]),
        ("", []),
        ("of mice", ["of"]),
    ),
    _fn(
        "strip_vowels",
        "strings",
        """
def strip_vowels(text):
    return "".join(ch for ch in text if ch.lower() not in "aeiou")
""",
        ("banana",),
        ("",),
        ("AEIxyz",),
        ("rhythm",),
    ),
    _fn(
        "longest_word",
        "strings",
        """
def longest_word(sentence):
    best = ""
    for word in sentence.split():
        if len(word) > len(best):
            best = word
    return best
""",
        ("the quick brown fox",),
        ("",),
        ("aa bb",),
        ("a abc ab",),
    ),
    _fn(
        "is_anagram",
        "strings",
        """
def is_anagram(first, second):
    if len(first) != len(second):
        return False
    counts = {}
    for ch in first:
        counts[ch] = counts.get(ch, 0) + 1
    for ch in second:
        if counts.get(ch, 0) == 0:
            return False
        counts[ch] -= 1
    return True
""",
        ("listen", "silent"),
        ("abc", "abd"),
        ("", ""),
        ("aab", "abb"),
        ("ab", "abc"),
    ),
    _fn(
        "wrap_text",
        "strings",
        """
def wrap_text(text, width):
    lines = []
    current = ""
    for word in text.split():
        if current and len(current) + 1 + len(word) > width:
            lines.append(current)
            current = word
        elif current:
            current += " " + word
        else:
            current = word
    if current:
        lines.append(current)
    return lines
""",
        ("the quick brown fox jumps", 10),
        ("", 5),
        ("a b c", 1),
        ("hello world", 11),
    ),
    _fn(
        "mask_digits",
        "strings",
        """
def mask_digits(text, keep):
    digits = [i for i, ch in enumerate(text) if ch.isdigit()]
    hidden = set(digits[:max(0, len(digits) - keep)])
    return "".join("*" if i in hidden else ch for i, ch in enumerate(text))
""",
        ("1234-5678", 4),
        ("abc", 2),
        ("12", 5),
        ("9 8 7", 1),
    ),
]

LISTS = [
    _fn(
        "running_sum",
        "lists",
        """
def running_sum(values):
    total = 0
    result = []
    for value in values:
        total += value
        result.append(total)
    return result
""",
        ([1, 2, 3],),
        ([],),
        ([5, -2, 7, 0],),
        ([10],),
    ),
    _fn(
        "remove_duplicates",
        "lists",
        """
def remove_duplicates(items):
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
""",
        ([1, 2, 1, 3, 2],),
        ([],),
        (["a", "a"],),
        ([4, 5, 6],),
    ),
    _fn(
        "chunk",
        "lists",
        """
def chunk(items, size):
    result = []
    for start in range(0, len(items), size):
        result.append(items[start:start + size])
    return result
""",
        ([1, 2, 3, 4, 5], 2),
        ([], 3),
        ([1, 2, 3], 3),
        ([1, 2, 3, 4], 1),
    ),
    _fn(
        "flatten",
        "lists",
        """
def flatten(nested):
    result = []
    for sub in nested:
        for item in sub:
            result.append(item)
    return result
""",
        ([[1, 2], [3]],),
        ([],),
        ([[], [4]],),
        ([[5], [6, 7], [8]],),
    ),
    _fn(
        "rotate",
        "lists",
        """
def rotate(items, k):
    if not items:
        return []
    k = k % len(items)
    return items[-k:] + items[:-k]
""",
        ([1, 2, 3, 4], 1),
        ([1, 2, 3], 0),
        ([], 2),
        ([1, 2, 3], 4),
        ([1, 2, 3, 4, 5], 2),
    ),
    _fn(
        "interleave",
        "lists",
        """
def interleave(a, b):
    result = []
    for i in range(max(len(a), len(b))):
        if i < len(a):
            result.append(a[i])
        if i < len(b):
            result.append(b[i])
    return result
""",
        ([1, 3], [2, 4]),
        ([1], [2, 3, 4]),
        ([], []),
        ([1, 2, 3], []),
    ),
    _fn(
        "pairwise_diff",
        "lists",
        """
def pairwise_diff(values):
    return [values[i + 1] - values[i] for i in range(len(values) - 1)]
""",
        ([1, 4, 9, 16],),
        ([],),
        ([5],),
        ([3, 1, 2],),
    ),
    _fn(
        "partition",
        "lists",
        """
def partition(values, pivot):
    low = []
    high = []
    for value in values:
        if value < pivot:
            low.append(value)
        else:
            high.append(value)
    return low, high
""",
        ([5, 1, 7, 3, 5], 5),
        ([], 0),
        ([1, 2], 3),
        ([4, 4], 4),
    ),
    _fn(
        "moving_average",
        "lists",
        """
def moving_average(values, window):
    result = []
    total = 0
    for i, value in enumerate(values):
        total += value
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            result.append(total / window)
    return result
""",
        ([1, 2, 3, 4, 5], 2),
        ([1, 2, 3], 3),
        ([], 2),
        ([4, 8, 6, 2], 1),
    ),
    _fn(
        "take_while_positive",
        "lists",
        """
def take_while_positive(values):
    result = []
    for value in values:
        if value <= 0:
            break
        result.append(value)
    return result
""",
        ([3, 2, 0, 5],),
        ([],),
        ([-1, 2],),
        ([1, 2, 3],),
    ),
    _fn(
        "zip_longest_fill",
        "lists",
        """
def zip_longest_fill(a, b, fill):
    size = max(len(a), len(b))
    pairs = []
    for i in range(size):
        left = a[i] if i < len(a) else fill
        right = b[i] if i < len(b) else fill
        pairs.append((left, right))
    return pairs
""",
        ([1, 2], [3], 0),
        ([], [], None),
        ([1], [2, 3, 4], -1),
    ),
    _fn(
        "split_at_zeros",
        "lists",
        """
def split_at_zeros(values):
    groups = [[]]
    for value in values:
        if value == 0:
            if groups[-1]:
                groups.append([])
        else:
            groups[-1].append(value)
    if not groups[-1]:
        groups.pop()
    return groups
""",
        ([1, 2, 0, 3, 0, 0, 4],),
        ([],),
        ([0, 0],),
        ([5, 6],),
    ),
    _fn(
        "last_index_of",
        "lists",
        """
def last_index_of(items, target):
    for i in range(len(items) - 1, -1, -1):
        if items[i] == target:
            return i
    return -1
""",
        ([1, 2, 1, 3], 1),
        ([], 5),
        ([4], 4),
        ([1, 2], 3),
    ),
    _fn(
        "compress_ranges",
        "lists",
        """
def compress_ranges(numbers):
    ranges = []
    for n in sorted(numbers):
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1][1] = n
        else:
            ranges.append([n, n])
    return [tuple(r) for r in ranges]
""",
        ([1, 2, 3, 7, 8, 10],),
        ([],),
        ([5],),
        ([3, 1, 2],),
    ),
]

MATH = [
    _fn(
        "factorial",
        "math",
        """
def factorial(n):
    result = 1
    for k in range(2, n + 1):
        result *= k
    return result
""",
        (0,),
        (1,),
        (5,),
        (7,),
    ),
    _fn(
        "fibonacci",
        "math",
        """
def fibonacci(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
""",
        (0,),
        (1,),
        (2,),
        (10,),
        (15,),
    ),
    _fn(
        "gcd",
        "math",
        """
def gcd(a, b):
    while b != 0:
        a, b = b, a % b
    return a
""",
        (12, 18),
        (7, 3),
        (0, 5),
        (100, 75),
        (9, 0),
    ),
    _fn(
        "is_prime",
        "math",
        """
def is_prime(n):
    if n < 2:
        return False
    i = 2
    while i * i <= n:
        if n % i == 0:
            return False
        i += 1
    return True
""",
        (1,),
        (2,),
        (9,),
        (13,),
        (25,),
        (29,),
    ),
    _fn(
        "digits_sum",
        "math",
        """
def digits_sum(n):
    n = abs(n)
    total = 0
    while n > 0:
        total += n % 10
        n //= 10
    return total
""",
        (0,),
        (123,),
        (-45,),
        (9999,),
    ),
    _fn(
        "clamp",
        "math",
        """
def clamp(value, low, high):
    if value < low:
        return low
    if value > high:
        return high
    return value
""",
        (5, 0, 10),
        (-3, 0, 10),
        (12, 0, 10),
        (0, 0, 10),
        (10, 0, 10),
    ),
    _fn(
        "average",
        "math",
        """
def average(values):
    if not values:
        return 0.0
    return sum(values) / len(values)
""",
        ([1, 2, 3],),
        ([],),
        ([10],),
        ([2, 4, 4, 4, 5, 5, 7, 9],),
    ),
    _fn(
        "power_mod",
        "math",
        """
def power_mod(base, exponent, modulus):
    result = 1
    base = base % modulus
    while exponent > 0:
        if exponent % 2 == 1:
            result = result * base % modulus
        base = base * base % modulus
        exponent //= 2
    return result
""",
        (2, 10, 1000),
        (3, 0, 7),
        (5, 3, 13),
        (7, 5, 1),
    ),
    _fn(
        "to_binary",
        "math",
        """
def to_binary(n):
    if n == 0:
        return "0"
    bits = []
    while n > 0:
        bits.append(str(n % 2))
        n //= 2
    return "".join(reversed(bits))
""",
        (0,),
        (1,),
        (5,),
        (18,),
        (255,),
    ),
    _fn(
        "collatz_steps",
        "math",
        """
def collatz_steps(n):
    steps = 0
    while n != 1:
        if n % 2 == 0:
            n //= 2
        else:
            n = 3 * n + 1
        steps += 1
    return steps
""",
        (1,),
        (2,),
        (6,),
        (27,),
    ),
    _fn(
        "sum_of_multiples",
        "math",
        """
def sum_of_multiples(limit, a, b):
    total = 0
    for n in range(1, limit):
        if n % a == 0 or n % b == 0:
            total += n
    return total
""",
        (10, 3, 5),
        (1, 3, 5),
        (16, 2, 7),
        (20, 4, 4),
    ),
    _fn(
        "perfect_number",
        "math",
        """
def perfect_number(n):
    if n < 2:
        return False
    total = 1
    d = 2
    while d * d <= n:
        if n % d == 0:
            total += d
            if d != n // d:
                total += n // d
        d += 1
    return total == n
""",
        (6,),
        (28,),
        (12,),
        (1,),
        (496,),
    ),
    _fn(
        "triangle_type",
        "math",
        """
def triangle_type(a, b, c):
    if a + b <= c or a + c <= b or b + c <= a:
        return "invalid"
    if a == b == c:
        return "equilateral"
    if a == b or b == c or a == c:
        return "isosceles"
    return "scalene"
""",
        (3, 3, 3),
        (3, 3, 5),
        (3, 4, 5),
        (1, 2, 3),
        (5, 3, 3),
    ),
    _fn(
        "integer_sqrt",
        "math",
        """
def integer_sqrt(n):
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if mid * mid <= n:
            lo = mid
        else:
            hi = mid - 1
    return lo
""",
        (0,),
        (1,),
        (15,),
        (16,),
        (99,),
    ),
]

SEARCH_SORT = [
    _fn(
        "binary_search",
        "search_sort",
        """
def binary_search(items, target):
    lo, hi = 0, len(items) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if items[mid] == target:
            return mid
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1
""",
        ([1, 3, 5, 7, 9], 7),
        ([1, 3, 5, 7, 9], 1),
        ([1, 3, 5, 7, 9], 9),
        ([2, 4], 3),
        ([], 1),
        ([5], 5),
    ),
    _fn(
        "is_sorted",
        "search_sort",
        """
def is_sorted(values):
    for i in range(len(values) - 1):
        if values[i] > values[i + 1]:
            return False
    return True
""",
        ([1, 2, 2, 3],),
        ([3, 1],),
        ([],),
        ([5],),
        ([1, 3, 2],),
    ),
    _fn(
        "max_index",
        "search_sort",
        """
def max_index(values):
    best = 0
    for i in range(1, len(values)):
        if values[i] > values[best]:
            best = i
    return best
""",
        ([3, 1, 4, 1, 5],),
        ([7],),
        ([2, 9, 9, 1],),
        ([-3, -1, -2],),
    ),
    _fn(
        "insertion_sort",
        "search_sort",
        """
def insertion_sort(values):
    items = list(values)
    for i in range(1, len(items)):
        key = items[i]
        j = i - 1
        while j >= 0 and items[j] > key:
            items[j + 1] = items[j]
            j -= 1
        items[j + 1] = key
    return items
""",
        ([3, 1, 2],),
        ([],),
        ([5, 4, 3, 2, 1],),
        ([1, 1, 0],),
    ),
    _fn(
        "merge_sorted",
        "search_sort",
        """
def merge_sorted(left, right):
    result = []
    i = j = 0
    while i < len(left) and j < len(right):
        if left[i] <= right[j]:
            result.append(left[i])
            i += 1
        else:
            result.append(right[j])
            j += 1
    result.extend(left[i:])
    result.extend(right[j:])
    return result
""",
        ([1, 4, 7], [2, 3, 8]),
        ([], [1, 2]),
        ([5], []),
        ([1, 1], [1]),
    ),
    _fn(
        "second_largest",
        "search_sort",
        """
def second_largest(values):
    first = second = None
    for value in values:
        if first is None or value > first:
            second = first
            first = value
        elif value != first and (second is None or value > second):
            second = value
    return second
""",
        ([3, 1, 4, 1, 5],),
        ([2, 2],),
        ([7],),
        ([1, 9, 8, 9],),
    ),
    _fn(
        "min_max",
        "search_sort",
        """
def min_max(values):
    low = high = values[0]
    for value in values[1:]:
        if value < low:
            low = value
        if value > high:
            high = value
    return low, high
""",
        ([3, 1, 4],),
        ([5],),
        ([-1, -5, 2, 2],),
    ),
    _fn(
        "count_inversions",
        "search_sort",
        """
def count_inversions(values):
    count = 0
    for i in range(len(values)):
        for j in range(i + 1, len(values)):
            if values[i] > values[j]:
                count += 1
    return count
""",
        ([1, 2, 3],),
        ([3, 2, 1],),
        ([],),
        ([2, 4, 1, 3, 5],),
    ),
    _fn(
        "find_peak",
        "search_sort",
        """
def find_peak(values):
    for i in range(len(values)):
        left = values[i - 1] if i > 0 else float("-inf")
        right = values[i + 1] if i + 1 < len(values) else float("-inf")
        if values[i] >= left and values[i] >= right:
            return i
    return -1
""",
        ([1, 3, 2],),
        ([5, 4, 3],),
        ([1, 2, 3],),
        ([],),
        ([2],),
    ),
    _fn(
        "bubble_sort_desc",
        "search_sort",
        """
def bubble_sort_desc(values):
    items = list(values)
    n = len(items)
    for i in range(n):
        for j in range(n - 1 - i):
            if items[j] < items[j + 1]:
                items[j], items[j + 1] = items[j + 1], items[j]
    return items
""",
        ([1, 3, 2],),
        ([],),
        ([4, 4, 5],),
        ([9, 1, 8, 2],),
    ),
    _fn(
        "lower_bound",
        "search_sort",
        """
def lower_bound(items, target):
    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi) // 2
        if items[mid] < target:
            lo = mid + 1
        else:
            hi = mid
    return lo
""",
        ([1, 2, 4, 4, 5], 4),
        ([1, 2, 3], 0),
        ([1, 2, 3], 9),
        ([], 1),
        ([2, 2], 2),
    ),
    _fn(
        "kth_smallest",
        "search_sort",
        """
def kth_smallest(values, k):
    items = sorted(values)
    if k < 1 or k > len(items):
        return None
    return items[k - 1]
""",
        ([5, 1, 4], 1),
        ([5, 1, 4], 3),
        ([5, 1, 4], 4),
        ([], 1),
        ([2, 2, 1], 2),
    ),
    _fn(
        "two_sum_sorted",
        "search_sort",
        """
def two_sum_sorted(values, target):
    i, j = 0, len(values) - 1
    while i < j:
        total = values[i] + values[j]
        if total == target:
            return i, j
        if total < target:
            i += 1
        else:
            j -= 1
    return None
""",
        ([1, 2, 4, 7, 11], 9),
        ([1, 2], 5),
        ([], 0),
        ([1, 3, 5, 7], 12),
    ),
    _fn(
        "rank_scores",
        "search_sort",
        """
def rank_scores(scores):
    ordered = sorted(set(scores), reverse=True)
    ranks = {}
    for position, score in enumerate(ordered):
        ranks[score] = position + 1
    return [ranks[score] for score in scores]
""",
        ([10, 20, 10, 30],),
        ([],),
        ([5],),
        ([1, 2, 3],),
    ),
]

DICTS = [
    _fn(
        "char_frequency",
        "dicts",
        """
def char_frequency(text):
    counts = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    return counts
""",
        ("abca",),
        ("",),
        ("zzz",),
        ("ab ab",),
    ),
    _fn(
        "count_words",
        "dicts",
        """
def count_words(text):
    counts = {}
    for word in text.lower().split():
        if word in counts:
            counts[word] += 1
        else:
            counts[word] = 1
    return counts
""",
        ("the cat the dog",),
        ("",),
        ("A a a",),
        ("x y",),
    ),
    _fn(
        "invert_dict",
        "dicts",
        """
def invert_dict(mapping):
    inverted = {}
    for key, value in mapping.items():
        inverted.setdefault(value, []).append(key)
    return inverted
""",
        ({"a": 1, "b": 2, "c": 1},),
        ({},),
        ({"x": "y"},),
    ),
    _fn(
        "group_by_length",
        "dicts",
        """
def group_by_length(words):
    groups = {}
    for word in words:
        size = len(word)
        if size not in groups:
            groups[size] = []
        groups[size].append(word)
    return groups
""",
        (["a", "bb", "cc", "d"],),
        ([],),
        (["xyz"],),
    ),
    _fn(
        "merge_counts",
        "dicts",
        """
def merge_counts(first, second):
    merged = dict(first)
    for key, value in second.items():
        merged[key] = merged.get(key, 0) + value
    return merged
""",
        ({"a": 1}, {"a": 2, "b": 3}),
        ({}, {}),
        ({"x": 5}, {}),
    ),
    _fn(
        "most_common",
        "dicts",
        """
def most_common(items):
    counts = {}
    best = None
    for item in items:
        counts[item] = counts.get(item, 0) + 1
        if best is None or counts[item] > counts[best]:
            best = item
    return best
""",
        ([1, 2, 2, 3],),
        ([],),
        (["a", "b", "b", "a", "a"],),
        ([7],),
    ),
    _fn(
        "filter_by_value",
        "dicts",
        """
def filter_by_value(mapping, threshold):
    return {key: value for key, value in mapping.items() if value >= threshold}
""",
        ({"a": 1, "b": 5, "c": 3}, 3),
        ({}, 0),
        ({"x": 2}, 3),
    ),
    _fn(
        "nested_get",
        "dicts",
        """
def nested_get(data, path, default):
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current
""",
        ({"a": {"b": {"c": 1}}}, ["a", "b", "c"], None),
        ({"a": 1}, ["a", "b"], 0),
        ({}, [], "x"),
        ({"a": {"b": 2}}, ["a", "z"], -1),
    ),
    _fn(
        "key_with_max_value",
        "dicts",
        """
def key_with_max_value(mapping):
    best_key = None
    best_value = None
    for key, value in mapping.items():
        if best_value is None or value > best_value:
            best_key = key
            best_value = value
    return best_key
""",
        ({"a": 3, "b": 7, "c": 5},),
        ({},),
        ({"x": -1, "y": -2},),
    ),
    _fn(
        "histogram",
        "dicts",
        """
def histogram(values, bucket):
    counts = {}
    for value in values:
        start = value // bucket * bucket
        counts[start] = counts.get(start, 0) + 1
    return dict(sorted(counts.items()))
""",
        ([1, 5, 12, 15, 21], 10),
        ([], 5),
        ([0, 4, 5], 5),
    ),
    _fn(
        "diff_dicts",
        "dicts",
        """
def diff_dicts(old, new):
    added = sorted(key for key in new if key not in old)
    removed = sorted(key for key in old if key not in new)
    changed = sorted(key for key in old if key in new and old[key] != new[key])
    return added, removed, changed
""",
        ({"a": 1, "b": 2}, {"b": 3, "c": 4}),
        ({}, {}),
        ({"x": 1}, {"x": 1}),
    ),
    _fn(
        "index_by",
        "dicts",
        """
def index_by(records, field):
    index = {}
    for record in records:
        key = record.get(field)
        if key is None:
            continue
        index[key] = record
    return index
""",
        ([{"id": 1, "n": "a"}, {"id": 2}, {"n": "c"}], "id"),
        ([], "id"),
        ([{"id": 1}, {"id": 1, "v": 2}], "id"),
    ),
    _fn(
        "top_keys",
        "dicts",
        """
def top_keys(mapping, n):
    ordered = sorted(mapping.items(), key=lambda item: (-item[1], item[0]))
    return [key for key, _ in ordered[:n]]
""",
        ({"a": 1, "b": 3, "c": 2}, 2),
        ({}, 1),
        ({"x": 1, "y": 1}, 1),
        ({"a": 5}, 0),
    ),
    _fn(
        "count_by_first_letter",
        "dicts",
        """
def count_by_first_letter(words):
    counts = {}
    for word in words:
        if not word:
            continue
        letter = word[0].lower()
        counts[letter] = counts.get(letter, 0) + 1
    return counts
""",
        (["Apple", "avocado", "banana", ""],),
        ([],),
        (["x", "X", "y"],),
    ),
]

PARSING = [
    _fn(
        "parse_key_values",
        "parsing",
        """
def parse_key_values(text):
    result = {}
    for part in text.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        result[key.strip()] = value.strip()
    return result
""",
        ("a=1; b = 2;c",),
        ("",),
        ("x=y=z",),
        ("k=v;k=w",),
    ),
    _fn(
        "parse_duration",
        "parsing",
        """
def parse_duration(text):
    units = {"h": 3600, "m": 60, "s": 1}
    total = 0
    number = ""
    for ch in text:
        if ch.isdigit():
            number += ch
        elif ch in units and number:
            total += int(number) * units[ch]
            number = ""
        else:
            return None
    return total if not number else None
""",
        ("1h30m",),
        ("45s",),
        ("",),
        ("10",),
        ("2h5x",),
        ("1m1s",),
    ),
    _fn(
        "parse_version",
        "parsing",
        """
def parse_version(text):
    parts = text.split(".")
    numbers = []
    for part in parts:
        if not part.isdigit():
            return None
        numbers.append(int(part))
    while len(numbers) < 3:
        numbers.append(0)
    return tuple(numbers)
""",
        ("1.2.3",),
        ("2",),
        ("1.x",),
        ("10.0",),
        ("",),
    ),
    _fn(
        "tokenize_expression",
        "parsing",
        """
def tokenize_expression(text):
    tokens = []
    number = ""
    for ch in text:
        if ch.isdigit():
            number += ch
            continue
        if number:
            tokens.append(number)
            number = ""
        if ch in "+-*/()":
            tokens.append(ch)
    if number:
        tokens.append(number)
    return tokens
""",
        ("12+3*(4-1)",),
        ("",),
        ("7",),
        (" 1 + 22 ",),
    ),
    _fn(
        "parse_csv_line",
        "parsing",
        """
def parse_csv_line(line):
    fields = []
    current = ""
    quoted = False
    for ch in line:
        if ch == '"':
            quoted = not quoted
        elif ch == "," and not quoted:
            fields.append(current)
            current = ""
        else:
            current += ch
    fields.append(current)
    return fields
""",
        ("a,b,c",),
        ('"x,y",z',),
        ("",),
        ("1,,2",),
    ),
    _fn(
        "parse_range_list",
        "parsing",
        """
def parse_range_list(text):
    numbers = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-")
            numbers.extend(range(int(start), int(end) + 1))
        else:
            numbers.append(int(part))
    return numbers
""",
        ("1-3,5",),
        ("",),
        ("7",),
        ("2-2, 4-5",),
    ),
    _fn(
        "balanced_brackets",
        "parsing",
        """
def balanced_brackets(text):
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for ch in text:
        if ch in "([{":
            stack.append(ch)
        elif ch in pairs:
            if not stack or stack[-1] != pairs[ch]:
                return False
            stack.pop()
    return not stack
""",
        ("([]{})",),
        ("(]",),
        ("",),
        ("((",),
        ("a(b)c",),
    ),
    _fn(
        "parse_query_string",
        "parsing",
        """
def parse_query_string(query):
    params = {}
    if query.startswith("?"):
        query = query[1:]
    for pair in query.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        params.setdefault(key, []).append(value)
    return params
""",
        ("?a=1&b=2&a=3",),
        ("",),
        ("x",),
        ("k=&k=v",),
    ),
    _fn(
        "roman_to_int",
        "parsing",
        """
def roman_to_int(text):
    values = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    for i, ch in enumerate(text):
        value = values[ch]
        if i + 1 < len(text) and values[text[i + 1]] > value:
            total -= value
        else:
            total += value
    return total
""",
        ("III",),
        ("IV",),
        ("MCMXCIV",),
        ("",),
        ("XL",),
    ),
    _fn(
        "split_camel_case",
        "parsing",
        """
def split_camel_case(name):
    words = []
    current = ""
    for ch in name:
        if ch.isupper() and current:
            words.append(current)
            current = ch.lower()
        else:
            current += ch.lower()
    if current:
        words.append(current)
    return words
""",
        ("camelCaseName",),
        ("",),
        ("X",),
        ("parseHTTP",),
    ),
]

SECURITY = [
    _fn(
        "safe_join",
        "security",
        """
def safe_join(base, name):
    if name.startswith("/") or "\\\\" in name:
        raise PermissionError("absolute or windows path")
    parts = []
    for part in name.split("/"):
        if part == "..":
            raise PermissionError("path traversal")
        if part and part != ".":
            parts.append(part)
    return base.rstrip("/") + "/" + "/".join(parts)
""",
        ("/srv/data", "a/b.txt"),
        ("/srv/data", "../etc/passwd"),
        ("/srv/data", "/etc/passwd"),
        ("/srv/data/", "./x"),
        ("/srv/data", "a/../../b"),
    ),
    _fn(
        "check_role",
        "security",
        """
def check_role(user, required):
    levels = {"guest": 0, "user": 1, "admin": 2}
    if user.get("disabled"):
        return False
    level = levels.get(user.get("role"), -1)
    return level >= levels[required]
""",
        ({"role": "admin"}, "user"),
        ({"role": "guest"}, "user"),
        ({"role": "user", "disabled": True}, "user"),
        ({"role": "root"}, "guest"),
        ({"role": "user"}, "user"),
    ),
    _fn(
        "validate_port",
        "security",
        """
def validate_port(value):
    if not value.isdigit():
        raise ValueError("not a number")
    port = int(value)
    if port < 1 or port > 65535:
        raise ValueError("out of range")
    return port
""",
        ("80",),
        ("0",),
        ("65535",),
        ("65536",),
        ("-1",),
        ("8080x",),
    ),
    _fn(
        "allowlisted_host",
        "security",
        """
def allowlisted_host(url, allowed):
    if not url.startswith("https://"):
        return False
    host = url[len("https://"):].split("/", 1)[0].split(":", 1)[0].lower()
    return host in allowed
""",
        ("https://api.example.com/v1", ["api.example.com"]),
        ("http://api.example.com", ["api.example.com"]),
        ("https://evil.com/api.example.com", ["api.example.com"]),
        ("https://API.example.com:443/x", ["api.example.com"]),
    ),
    _fn(
        "bounded_copy",
        "security",
        """
def bounded_copy(data, offset, length, limit):
    if offset < 0 or length < 0:
        raise ValueError("negative")
    if offset + length > len(data) or length > limit:
        raise ValueError("out of bounds")
    return data[offset:offset + length]
""",
        ([1, 2, 3, 4], 1, 2, 8),
        ([1, 2, 3], 2, 2, 8),
        ([1, 2, 3], -1, 1, 8),
        ([1, 2, 3], 0, 3, 2),
        ([1, 2, 3], 0, 3, 3),
    ),
    _fn(
        "sanitize_filename",
        "security",
        """
def sanitize_filename(name):
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789._-"
    cleaned = "".join(ch for ch in name.lower() if ch in allowed)
    cleaned = cleaned.lstrip(".")
    if not cleaned:
        raise ValueError("empty name")
    return cleaned[:64]
""",
        ("Report 2024.pdf",),
        ("../../etc/passwd",),
        ("...",),
        (".hidden",),
        ("a" * 70,),
    ),
    _fn(
        "rate_limit_ok",
        "security",
        """
def rate_limit_ok(timestamps, now, window, limit):
    recent = [t for t in timestamps if now - t < window]
    return len(recent) < limit
""",
        ([1, 2, 3], 4, 10, 3),
        ([1, 2, 3], 20, 10, 3),
        ([], 0, 10, 1),
        ([5, 9], 10, 5, 2),
        ([6, 9], 10, 5, 2),
    ),
    _fn(
        "verify_password_policy",
        "security",
        """
def verify_password_policy(password):
    if len(password) < 12:
        return False
    has_digit = any(ch.isdigit() for ch in password)
    has_upper = any(ch.isupper() for ch in password)
    has_lower = any(ch.islower() for ch in password)
    return has_digit and has_upper and has_lower
""",
        ("Short1A",),
        ("longenoughpassword1",),
        ("LongEnoughPassword",),
        ("LongEnoughPass1",),
        ("ALLUPPERCASE123",),
    ),
    _fn(
        "escape_html",
        "security",
        """
def escape_html(text):
    replacements = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#x27;"}
    return "".join(replacements.get(ch, ch) for ch in text)
""",
        ("<script>alert(1)</script>",),
        ("a & b",),
        ("",),
        ('say "x"',),
    ),
    _fn(
        "parameterize_query",
        "security",
        """
def parameterize_query(table, filters, allowed_columns):
    if table not in ("users", "orders"):
        raise ValueError("unknown table")
    clauses = []
    params = []
    for column, value in sorted(filters.items()):
        if column not in allowed_columns:
            raise ValueError("unknown column")
        clauses.append(column + " = ?")
        params.append(value)
    sql = "SELECT * FROM " + table
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return sql, params
""",
        ("users", {"name": "bob"}, ["name", "id"]),
        ("users;drop", {}, ["id"]),
        ("orders", {"x": 1}, ["id"]),
        ("orders", {}, ["id"]),
        ("users", {"id": 1, "name": "a"}, ["name", "id"]),
    ),
    _fn(
        "constant_time_equal",
        "security",
        """
def constant_time_equal(a, b):
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a, b):
        result |= ord(x) ^ ord(y)
    return result == 0
""",
        ("secret", "secret"),
        ("secret", "secreT"),
        ("", ""),
        ("abc", "ab"),
    ),
    _fn(
        "session_valid",
        "security",
        """
def session_valid(session, now, max_age):
    if session.get("revoked"):
        return False
    created = session.get("created")
    if created is None or created > now:
        return False
    return now - created <= max_age
""",
        ({"created": 100}, 150, 60),
        ({"created": 100}, 200, 60),
        ({"created": 100, "revoked": True}, 110, 60),
        ({}, 10, 60),
        ({"created": 300}, 200, 60),
    ),
    _fn(
        "mask_secret",
        "security",
        """
def mask_secret(value, visible):
    if visible < 0:
        raise ValueError("negative")
    if len(value) <= visible:
        return "*" * len(value)
    return "*" * (len(value) - visible) + value[len(value) - visible:]
""",
        ("abcdef1234", 4),
        ("abc", 4),
        ("secret", 0),
        ("x", -1),
    ),
    _fn(
        "check_upload",
        "security",
        """
def check_upload(name, size, max_size):
    allowed = {"png", "jpg", "pdf"}
    if "." not in name:
        return False
    extension = name.rsplit(".", 1)[1].lower()
    if extension not in allowed:
        return False
    return 0 < size <= max_size
""",
        ("photo.PNG", 100, 1000),
        ("run.exe", 10, 1000),
        ("doc.pdf", 5000, 1000),
        ("noext", 1, 10),
        ("a.jpg", 0, 10),
        ("x.pdf.exe", 5, 10),
    ),
    _fn(
        "validate_redirect",
        "security",
        """
def validate_redirect(target):
    if not target.startswith("/") or target.startswith("//"):
        return "/"
    if "\\\\" in target or "\\n" in target:
        return "/"
    return target
""",
        ("/home",),
        ("//evil.com",),
        ("https://evil.com",),
        ("/ok\npath",),
        ("/a/b?c=1",),
    ),
    _fn(
        "quota_remaining",
        "security",
        """
def quota_remaining(used, quota, request):
    if request <= 0:
        raise ValueError("invalid request")
    remaining = quota - used
    if request > remaining:
        raise PermissionError("quota exceeded")
    return remaining - request
""",
        (5, 10, 3),
        (5, 10, 5),
        (5, 10, 6),
        (0, 10, 0),
        (9, 10, 1),
    ),
]

ALL: tuple[ReferenceFunction, ...] = tuple(
    STRINGS + LISTS + MATH + SEARCH_SORT + DICTS + PARSING + SECURITY
)

#: Familles partagées entre train / validation / held-out.
SHARED_CATEGORIES = ("strings", "lists", "math", "search_sort", "dicts")
#: Famille jamais vue à l'entraînement : held-out seulement (généralisation).
NOVEL_CATEGORIES = ("parsing",)
#: Famille de transfert : suite séparée (défensive, jamais vue à l'entraînement).
TRANSFER_CATEGORIES = ("security",)
