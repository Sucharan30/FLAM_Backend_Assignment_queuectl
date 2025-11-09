import subprocess

def run_command(command: str, timeout: int | None = None) -> tuple[int, str]:
    """
    Executes shell command. Returns (returncode, stderr_or_empty).
    """
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
        err = (r.stderr or "").strip()
        return r.returncode, err
    except Exception as e:
        return 1, f"exception: {e}"
