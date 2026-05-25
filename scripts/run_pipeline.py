#!/usr/bin/env python3
"""
run_pipeline.py - Unified pipeline orchestrator
Stages: crawl -> extract -> LLM label -> merge -> top questions
"""

import os
import subprocess
import sys
import time

if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))

CRAWL_STEPS = [
    ("discover_nowcoder_search.py", "Discover Nowcoder posts"),
    ("fetch_nowcoder_posts.py", "Fetch post content"),
    ("clean_nowcoder_posts.py", "Clean and classify posts"),
]

EXTRACT_STEPS = [
    ("extract_questions.py", "Extract questions from raw text"),
    ("fix_company_from_title.py", "Fix company from titles"),
]

LABEL_STEPS = [
    ("make_llm_batches.py", "Create LLM labeling batches"),
    ("run_llm_label_batches.py", "Run LLM labeling"),
    ("validate_llm_output.py", "Validate LLM output"),
    ("build_labeled_raw_table.py", "Build labeled raw table"),
]

MERGE_STEPS = [
    ("make_merge_candidates.py", "Generate merge candidates"),
    ("make_merge_review_batches.py", "Create merge review batches"),
    ("run_merge_batches.py", "Run LLM merge decisions"),
    ("validate_merge_output.py", "Validate merge output"),
    ("build_question_metadata.py", "Build question metadata"),
    ("apply_manual_question_overrides.py", "Apply manual overrides"),
    ("top_questions.py", "Generate top question exports"),
]


def run_step(script, desc):
    path = os.path.join(SCRIPTS_DIR, script)
    if not os.path.exists(path):
        print(f"ERROR: {path} not found", file=sys.stderr)
        return False

    print(f"\n{'='*60}")
    print(f"Step: {desc} ({script})")
    print(f"{'='*60}")

    start = time.time()
    try:
        subprocess.run(
            [sys.executable, path],
            cwd=os.path.dirname(os.path.dirname(path)),
            check=True,
        )
        elapsed = time.time() - start
        print(f"DONE ({elapsed:.1f}s)")
        return True
    except subprocess.CalledProcessError as e:
        elapsed = time.time() - start
        print(f"FAILED ({elapsed:.1f}s), exit code: {e.returncode}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"EXCEPTION: {e}", file=sys.stderr)
        return False


def run_stage(steps, failed, interactive=True):
    for script, desc in steps:
        ok = run_step(script, desc)
        if not ok:
            failed.append(script)
            if not interactive:
                print(f"\nStep {script} failed. Aborting stage.", file=sys.stderr)
                break
            print(f"\nStep {script} failed. Continue? (y/n) ", end="")
            try:
                choice = input().strip().lower()
                if choice != "y":
                    print("Pipeline aborted.")
                    return False
            except (EOFError, KeyboardInterrupt):
                print("\nPipeline aborted.")
                return False
    return True


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Agent interview question pipeline")
    parser.add_argument("--skip-crawl", action="store_true", help="Skip crawl stage")
    parser.add_argument("--crawl-only", action="store_true", help="Only run crawl stage")
    parser.add_argument("--skip-label", action="store_true", help="Skip LLM labeling stage")
    parser.add_argument("--skip-merge", action="store_true", help="Skip merge stage")
    parser.add_argument("--extract-only", action="store_true", help="Only run through extraction")
    parser.add_argument("--non-interactive", action="store_true", help="Abort on first failure")
    args = parser.parse_args()

    print("Agent Interview Question Pipeline")
    print(f"Working directory: {os.path.dirname(SCRIPTS_DIR)}")

    total_start = time.time()
    failed = []
    interactive = not args.non_interactive

    # Stage 1: Crawl
    if not args.skip_crawl:
        print("\n--- Stage 1: Crawl ---")
        ok = run_stage(CRAWL_STEPS, failed, interactive)
        if not ok:
            return

    if args.crawl_only:
        total_elapsed = time.time() - total_start
        print(f"\nCrawl complete ({total_elapsed:.1f}s)")
        return

    # Stage 2: Extract
    print("\n--- Stage 2: Extract ---")
    ok = run_stage(EXTRACT_STEPS, failed, interactive)
    if not ok:
        return

    if args.extract_only:
        total_elapsed = time.time() - total_start
        print(f"\nExtraction complete ({total_elapsed:.1f}s)")
        return

    # Stage 3: LLM Label
    if not args.skip_label:
        print("\n--- Stage 3: LLM Labeling ---")
        ok = run_stage(LABEL_STEPS, failed, interactive)
        if not ok:
            return

    # Stage 4: Merge
    if not args.skip_merge:
        print("\n--- Stage 4: Merge + Build ---")
        ok = run_stage(MERGE_STEPS, failed, interactive)
        if not ok:
            return

    total_elapsed = time.time() - total_start

    print(f"\n{'='*60}")
    print(f"Pipeline complete ({total_elapsed:.1f}s)")
    if failed:
        print(f"Failed steps: {', '.join(failed)}")
    else:
        print("All steps succeeded!")

    # Check output
    data_dir = os.path.join(os.path.dirname(SCRIPTS_DIR), "data", "output")
    if os.path.isdir(data_dir):
        print("\nOutput files:")
        for f in sorted(os.listdir(data_dir)):
            path = os.path.join(data_dir, f)
            size = os.path.getsize(path) if os.path.isfile(path) else 0
            print(f"  {f} ({size:,} bytes)")


if __name__ == "__main__":
    main()
