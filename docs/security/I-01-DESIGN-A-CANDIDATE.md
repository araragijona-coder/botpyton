# I-01 Design A — isolated simulation candidate

Status: **CANDIDATE / SIMULATION ONLY / I-01 OPEN**

## Scope

This branch is a development candidate based on the audited I-01 branch. It adds a strict request contract and an in-memory simulation gate. It does not establish the final trust boundary: this code is still in the same repository as the worker and can be changed by repository contributors.

## Safety properties of this prototype

- No GitHub client, HTTP library, subprocess, filesystem access, token lookup, or credential handling in bot/i01_emitter_simulation.py.
- The caller supplies the authenticated actor and independently resolved head SHA; request fields cannot self-assert either fact.
- Request keys are closed and exact; unknown and missing fields fail closed.
- Repository, base ref, head-ref prefix, full lowercase SHA, text bounds, and draft=true are checked independently.
- A valid request can only append a simulation record to an in-memory recorder.
- Invalid requests and duplicate request IDs must leave the recorder unchanged.
- No workflow is enabled and no live PR is created by this candidate.

## Important limitation

The module is not a production sender and must not be connected to a GitHub API client. Its policy object must eventually be created from independently reviewed deployment configuration in a separate trusted repository/service. Authentication of the actor and resolution of the head SHA are assumed inputs here and are not implemented or verified by this prototype.

The definitive Design A trust boundary still requires a separately controlled emitter, isolated credentials, independently verified repository/Actions administration, and a separate approval to consider any live write path.

## Validation

The unit suite is intended to run under the existing Python 3.12 test workflow:

    python -m unittest tests.test_i01_emitter_simulation -v
    python -m unittest discover -s tests -v

I-01 remains **OPEN / FAIL-CLOSED / NOT CLEARED FOR LIVE SUBMISSION** until administrative evidence and all exit criteria are independently verified.
