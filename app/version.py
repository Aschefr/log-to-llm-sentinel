import subprocess
import os

def get_app_version():
    """
    Returns version in format: Major.MergeCount.CommitCount
    Example: 1.5.234
    """
    major = "1"
    
    try:
        # Get total number of commits
        commit_count = subprocess.check_output(["git", "rev-list", "--count", "HEAD"], stderr=subprocess.STDOUT).decode("utf-8").strip()
        
        # Get number of merges into main
        merge_count = subprocess.check_output(["git", "rev-list", "--count", "--merges", "HEAD"], stderr=subprocess.STDOUT).decode("utf-8").strip()
        
        return f"{major}.{merge_count}.{commit_count}"
    except Exception:
        # Fallback if git is not available or not a repo
        return f"{major}.0.0-dev"
