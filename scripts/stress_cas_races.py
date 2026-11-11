"""
stress_cas_races.py — Empirical CAS Concurrency Stress Harness
Authored by: teamwork_challenger_m1_it5_2

Stress-tests TaskManager CAS locking, lease renewal, theft, and lifecycle transitions
under high thread contention (60-100 threads).
"""
import concurrent.futures
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple

# Add shorts-agent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.modules.orchestration import (
    TaskManager,
    TaskCollisionError,
    StaleLockTokenError,
    InvalidStateTransitionError,
)

def run_stress_suite():
    print("=== Starting CAS Concurrency Empirical Stress Harness ===")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        tmp_path = Path(tmp_dir)

        # -------------------------------------------------------------
        # TEST 1: 100 Concurrent Threads Competing for a Single Task
        # -------------------------------------------------------------
        print("\n--- Test 1: 100 Concurrent Threads Racing on Single Task ---")
        db_path = tmp_path / "cas_100_race.sqlite"
        task_md = tmp_path / "task-ownership-1.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            conn.execute(
                "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                ("TASK-SINGLE-100", "Single Task Race")
            )
            conn.commit()

        thread_count = 100
        barrier = threading.Barrier(thread_count)
        winners: List[Tuple[str, str]] = []
        collisions: List[Tuple[str, str]] = []
        unexpected_errors: List[Tuple[str, Exception]] = []
        lock = threading.Lock()

        def race_worker(idx: int):
            agent = f"worker-{idx:03d}"
            try:
                barrier.wait(timeout=10.0)
                tok = manager.claim_task("TASK-SINGLE-100", agent_id=agent, lease_seconds=60)
                with lock:
                    winners.append((agent, tok))
            except TaskCollisionError as exc:
                with lock:
                    collisions.append((agent, str(exc)))
            except Exception as exc:
                with lock:
                    unexpected_errors.append((agent, exc))

        threads = [threading.Thread(target=race_worker, args=(i,)) for i in range(thread_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors encountered: {unexpected_errors}"
        assert len(winners) == 1, f"Expected exactly 1 winner, got {len(winners)}"
        assert len(collisions) == 99, f"Expected 99 collisions, got {len(collisions)}"

        task = manager.get_task("TASK-SINGLE-100")
        assert task["status"] == "IN_PROGRESS"
        assert task["owner"] == winners[0][0]
        assert task["lock_token"] == winners[0][1]
        print(f"  [PASS] Exactly 1 winner ({winners[0][0]}) and 99 clean collisions.")

        # -------------------------------------------------------------
        # TEST 2: 100 Threads Racing across 10 Distinct Tasks
        # -------------------------------------------------------------
        print("\n--- Test 2: 100 Threads Racing across 10 Distinct Tasks ---")
        db_path = tmp_path / "cas_10x10.sqlite"
        task_md = tmp_path / "task-ownership-2.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        task_ids = [f"TASK-GROUP-{i:02d}" for i in range(10)]
        with manager._get_conn() as conn:
            for tid in task_ids:
                conn.execute(
                    "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                    (tid, f"Group Task {tid}")
                )
            conn.commit()

        barrier = threading.Barrier(100)
        task_winners: Dict[str, Tuple[str, str]] = {}
        collision_count = 0
        unexpected_errors.clear()

        def group_worker(idx: int):
            target_task = task_ids[idx % 10]
            agent = f"group-agent-{idx:03d}"
            nonlocal collision_count
            try:
                barrier.wait(timeout=10.0)
                tok = manager.claim_task(target_task, agent_id=agent, lease_seconds=60)
                with lock:
                    task_winners[target_task] = (agent, tok)
            except TaskCollisionError:
                with lock:
                    collision_count += 1
            except Exception as exc:
                with lock:
                    unexpected_errors.append((agent, exc))

        threads = [threading.Thread(target=group_worker, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
        assert len(task_winners) == 10, f"Expected 10 winners, got {len(task_winners)}"
        assert collision_count == 90, f"Expected 90 collisions, got {collision_count}"
        assert set(task_winners.keys()) == set(task_ids)
        print(f"  [PASS] 10 tasks claimed by 10 distinct agents, exactly 90 collisions.")

        # -------------------------------------------------------------
        # TEST 3: Expired Lease Takeover Race under 60-Worker Contention
        # -------------------------------------------------------------
        print("\n--- Test 3: Expired Lease Takeover Race with 60 Workers ---")
        db_path = tmp_path / "cas_expired_steal.sqlite"
        task_md = tmp_path / "task-ownership-3.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        with manager._get_conn() as conn:
            conn.execute("""
            INSERT INTO task_registry 
            (task_id, task_name, owner, status, lock_token, locked_at, lease_expires_at)
            VALUES (?, ?, ?, 'IN_PROGRESS', ?, datetime('now', '-300 seconds'), datetime('now', '-10 seconds'))
            """, ("TASK-EXPIRED-TEST", "Expired Steal Task", "old-owner-agent", "old-token-xyz"))
            conn.commit()

        barrier = threading.Barrier(60)
        steal_winners: List[Tuple[str, str]] = []
        steal_collisions = 0
        unexpected_errors.clear()

        def steal_worker(idx: int):
            agent = f"stealer-{idx:03d}"
            nonlocal steal_collisions
            try:
                barrier.wait(timeout=10.0)
                tok = manager.claim_task("TASK-EXPIRED-TEST", agent_id=agent, lease_seconds=90)
                with lock:
                    steal_winners.append((agent, tok))
            except TaskCollisionError:
                with lock:
                    steal_collisions += 1
            except Exception as exc:
                with lock:
                    unexpected_errors.append((agent, exc))

        threads = [threading.Thread(target=steal_worker, args=(i,)) for i in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=25.0)

        assert len(unexpected_errors) == 0, f"Unexpected errors: {unexpected_errors}"
        assert len(steal_winners) == 1, f"Expected 1 winner to steal expired lease, got {len(steal_winners)}"
        assert steal_collisions == 59, f"Expected 59 collisions, got {steal_collisions}"

        # Former owner trying to heartbeat must now fail with StaleLockTokenError
        old_owner_failed = False
        try:
            manager.heartbeat("TASK-EXPIRED-TEST", agent_id="old-owner-agent", token="old-token-xyz")
        except StaleLockTokenError:
            old_owner_failed = True
        assert old_owner_failed is True, "Old owner was able to heartbeat after lease theft!"

        print(f"  [PASS] Lease stolen by {steal_winners[0][0]}, former owner heartbeat rejected.")

        # -------------------------------------------------------------
        # TEST 4: Full High-Concurrency Lifecycle Churn (60 threads, 15 tasks)
        # -------------------------------------------------------------
        print("\n--- Test 4: High-Concurrency Lifecycle Churn (60 threads, 15 tasks) ---")
        db_path = tmp_path / "cas_lifecycle.sqlite"
        task_md = tmp_path / "task-ownership-4.md"
        manager = TaskManager(db_path=db_path, task_md=task_md)

        all_task_ids = [f"CHURN-TASK-{i:02d}" for i in range(15)]
        with manager._get_conn() as conn:
            for tid in all_task_ids:
                conn.execute(
                    "INSERT INTO task_registry (task_id, task_name, owner, status) VALUES (?, ?, NULL, 'PENDING')",
                    (tid, f"Lifecycle Task {tid}")
                )
            conn.commit()

        completed_tasks: Set[str] = set()
        churn_lock = threading.Lock()
        churn_errors: List[Exception] = []

        def churn_worker(worker_id: int):
            agent = f"churn-agent-{worker_id:03d}"
            # Workers loop through tasks trying to claim, heartbeat, complete
            for tid in all_task_ids:
                try:
                    tok = manager.claim_task(tid, agent_id=agent, lease_seconds=30)
                    # Successfully claimed
                    time.sleep(0.01)
                    manager.heartbeat(tid, agent_id=agent, token=tok, extension_seconds=40)
                    time.sleep(0.01)
                    manager.complete_task(tid, agent_id=agent, token=tok, artifact_path=f"out_{tid}.mp4")
                    with churn_lock:
                        completed_tasks.add(tid)
                except TaskCollisionError:
                    # Expected when another worker already claimed it
                    continue
                except Exception as exc:
                    with churn_lock:
                        churn_errors.append(exc)

        threads = [threading.Thread(target=churn_worker, args=(i,)) for i in range(60)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        assert len(churn_errors) == 0, f"Churn worker errors: {churn_errors}"
        assert len(completed_tasks) == 15, f"Expected all 15 tasks completed, got {len(completed_tasks)}"

        # Verify all tasks in SQLite are COMPLETED
        for tid in all_task_ids:
            t = manager.get_task(tid)
            assert t["status"] == "COMPLETED"
            assert t["artifact_path"] == f"out_{tid}.mp4"

        # Verify Markdown sync integrity
        md_text = task_md.read_text(encoding="utf-8")
        for tid in all_task_ids:
            assert tid in md_text, f"Task {tid} missing from synced Markdown"
            assert "COMPLETED" in md_text or "Done" in md_text or "✅" in md_text

        print("  [PASS] All 15 tasks completed cleanly with zero corruption across 60 workers.")

    print("\n=== CAS Concurrency Empirical Stress Harness: ALL TESTS PASSED ===")

if __name__ == "__main__":
    run_stress_suite()
