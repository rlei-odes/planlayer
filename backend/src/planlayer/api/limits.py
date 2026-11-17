"""Upper bounds on what a request carries. Every text and list in a request body or query has
one (tests/test_limits.py checks), so runaway input is refused with 422 before it reaches the
store, whose text columns are unbounded. The bounds are generous for real use; a field with a
rule of its own (a note's body, a round's code) keeps its tighter one."""

from typing import Annotated

from pydantic import Field

# Codes and keys: member codes, logins, round codes, batch ids, fingerprints.
Code = Annotated[str, Field(max_length=100)]
# Names and titles: of members, investments, rounds, positions, users, rules.
Name = Annotated[str, Field(max_length=200)]
# An e-mail address (the longest one SMTP carries).
Email = Annotated[str, Field(max_length=254)]
# Free text: comments, reasons, descriptions.
LongText = Annotated[str, Field(max_length=2000)]
# A number as typed: an amount, a rate, a quantity, a percentage.
Number = Annotated[str, Field(max_length=40)]
# An admin's source query.
SqlQuery = Annotated[str, Field(max_length=100_000)]

# A list in a request (cost centers, accounts, mentions, ids): `Annotated[list[Code], MANY]`.
MANY = Field(max_length=10_000)
# The keys of a mapping in a request, such as a dataset's fields.
FEW = Field(max_length=100)
