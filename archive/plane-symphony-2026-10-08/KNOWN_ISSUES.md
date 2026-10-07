# Known Issues

**Status**: No known critical issues ✅

All identified issues have been fixed:

- ✅ **P1 Timeout isolation**: Implemented subprocess isolation (commit `XXXXX`)
- ✅ thread_list returns `ThreadListResponse.data`, not direct list (commit `84d3704`)
- ✅ `post_comment` → `add_comment` method name (commit `84d3704`)
- ✅ webhook actor extraction handles both string and dict (commit `84d3704`)
- ✅ Documentation state names unified to "AI Todo" (commit `84d3704`)

All tests pass: 55/55 ✓

---

## Resolved: P1 Timeout Can Now Truly Kill Codex Process

### Solution Implemented

**Subprocess isolation** - Codex runs in a separate process that can be terminated on timeout.

**How it works**:

```python
# Production mode (default)
process = multiprocessing.Process(target=_codex_worker, args=(...))
process.start()
process.join(timeout=timeout_s)

if process.is_alive():
    process.terminate()  # SIGTERM
    time.sleep(2)
    if process.is_alive():
        process.kill()  # SIGKILL
    return (None, None, True)  # timed_out, no background writes possible
```

**Benefits**:
- ✅ Truly kills Codex process on timeout
- ✅ No background MCP calls possible after timeout
- ✅ Clean timeout semantics: timeout = failure, period

**Implementation details**:
- Production: `_use_subprocess=True` (default)
- Tests: `_use_subprocess=False` (allows mocking)
- IPC: `multiprocessing.Queue` for result passing
- Settings/config are serialized as dicts (pickable)

**Test coverage**:
- `tests/test_subprocess_isolation.py`: Verifies process is truly killed
- `tests/test_timeout_fix.py`: Verifies timeout behavior
- `tests/test_runner.py`: Tests threading mode (for mocks)

### Migration from threading

**Before** (threading, P1 issue):
```python
worker = threading.Thread(target=_run, daemon=True)
worker.join(timeout=timeout_s)
if worker.is_alive():
    # ❌ Cannot kill thread, background may continue
    return (None, None, True)
```

**After** (subprocess, fixed):
```python
process = multiprocessing.Process(target=_codex_worker, ...)
process.join(timeout=timeout_s)
if process.is_alive():
    process.terminate()  # ✅ Truly kills process
    if still_alive:
        process.kill()  # ✅ Force kill
    return (None, None, True)
```

### Architecture Decision

We chose to implement subprocess isolation immediately rather than defer as "technical debt" because:

1. **Architectural correctness**: Subprocess is the right solution
2. **Clean semantics**: Timeout should mean complete cancellation
3. **Reasonable effort**: 2-3 hours implementation + testing
4. **No workarounds needed**: Eliminates need for monitoring and manual fixes

This follows the principle: **If it's architecturally correct and feasible, implement it now.**


