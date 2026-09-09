# Your agent's memory is a data-leak waiting to happen. Provenance fixes it.

*Technical article, HeadOfContext phase 1.*

## The problem nobody's memory tool solves

Agent memory is wonderful. Mem0, Zep, Graphiti and friends turn a stateless model into an
assistant that remembers what it learned last week. In an enterprise, that is exactly the
problem: **what it learned last week was learned while acting for someone else.**

An assistant helps an HR manager on Monday and reads the salary grid. On Tuesday a store
employee asks the same assistant "what's the raise this year?". The memory tool has a great
answer. The RAG layer would have filtered the salary grid out for the store employee. The memory
layer does not know there was a salary grid.

Permissioned retrieval (Glean, Onyx, PipesHub, Foundry IQ) protects the *documents*. It cannot
protect a *derived fact* that has been rewritten by a model and stored in a vector store under
the agent's own identity. Access control stops at the moment of learning.

## The rule

A memory inherits the rights of the documents it derives from, and those rights are checked
again every time the memory is read:

> `read(memory, subject)` is allowed if and only if `subject` is a viewer of **every** document
> in `derived_from(memory)` **now**.

Three consequences fall out:

- **Cross-user leaks disappear.** The store employee is not a viewer of the salary grid, so the
  memory is invisible to them, whatever the vector search says.
- **Revocation propagates to memories.** When the HR manager leaves HR, the memory they created
  goes dark for them too. There is no "cached knowledge" that survives an access removal.
- **Intersection, not union.** A memory that combines an HR document and a public one is as
  restricted as the HR one. Mixing sources can only tighten access.

A memory with no source at all (the agent learned it from the conversation) is readable only by
the person it was written for.

## Where provenance must live

The naive implementation stores `derived_from` as metadata next to the content in the memory
tool. That is where an attacker with the memory tool's credentials, or a bug in the tool's own
"memory consolidation", can erase it.

HeadOfContext keeps provenance in two places it controls, and reads **the union** of both:

1. a **provenance ledger** (a small PostgreSQL table): `memory_id → derived_from, written_for,
   written_by, content_hash, backend reference`;
2. **OpenFGA tuples**: `memory:X#derived_from@document:Y`, `memory:X#writer@agent:A`.

Removing a source from one store does not remove it from the check. A search hit whose backend
reference is not in the ledger has no provenance at all and is dropped before its content is
looked at.

The memory tool stores text and answers similarity queries. That is what it is good at, and
that is all it is trusted for. We also ask it to store content *verbatim* (`infer=False` in
Mem0): the sentence that was authorized is the sentence that gets stored and hashed.

## One decision path

The check is not memory-specific code. It is the same `Decider` that gates document reads and
tool calls, called with "all of these resources" semantics:

```
decide_all(chain, Action.read_memory(), sources, scope_resources=[memory_id])
```

- the agent's **scope** (from its biscuit token) must allow reading memories;
- OpenFGA must answer `viewer` for the **subject** on every source, in one BatchCheck;
- every candidate produces exactly one audit event, kept or dropped, with hashes and never
  content.

Because the subject comes from a verified token and the sources come from stores the agent
cannot write to, nothing the model says, and nothing a document says, can influence the
outcome. Prompt injection has no lever here.

## What it costs

One BatchCheck per candidate at read time, over a handful of documents. With OpenFGA next to the
service, the p95 stays well under the 20 ms budget the rest of the decision path lives with.
Writes cost one BatchCheck, one tuple write, one ledger insert.

## What it does not do

It does not decide *what* to remember, does not summarize, does not rank. It does not replace
the memory tool. It refuses to hand out a memory to someone who could not have read the
documents it came from. That is the whole job, and no memory tool does it today.

## Try it

`docs/quickstart.md` runs the HR / store-employee scenario in ten minutes against a real
OpenFGA, with a fictional company of 50 users and 500 documents. The adversarial tests in
`tests/adversarial/test_memory_leaks.py` are the attacks we expect to keep failing.
