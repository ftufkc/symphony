"""Test subprocess runner success path with real Queue IPC."""
import multiprocessing
import time


def _mock_worker(result_queue):
    """Minimal worker that returns success via Queue."""
    try:
        # Simulate work
        time.sleep(0.1)
        result_queue.put({"result": "SUCCESS", "thread_id": "thread-123"})
    except Exception as e:
        result_queue.put({"error": str(e), "error_type": type(e).__name__})


def _failing_worker(result_queue):
    """Worker that raises exception."""
    try:
        raise ValueError("test error")
    except Exception as e:
        result_queue.put({"error": str(e), "error_type": type(e).__name__})


def test_subprocess_worker_success_path():
    """Verify subprocess worker can return results via Queue."""
    # Start real subprocess
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_mock_worker, args=(result_queue,))

    process.start()
    process.join(timeout=2)

    # Verify process completed
    assert not process.is_alive()
    assert process.exitcode == 0

    # Verify result came through Queue
    result = result_queue.get(timeout=1)
    assert result["result"] == "SUCCESS"
    assert result["thread_id"] == "thread-123"


def test_subprocess_worker_exception_path():
    """Verify subprocess worker can return errors via Queue."""
    result_queue = multiprocessing.Queue()
    process = multiprocessing.Process(target=_failing_worker, args=(result_queue,))

    process.start()
    process.join(timeout=2)

    assert not process.is_alive()
    assert process.exitcode == 0  # Worker catches exception

    # Verify error came through Queue
    result = result_queue.get(timeout=1)
    assert result["error"] == "test error"
    assert result["error_type"] == "ValueError"
