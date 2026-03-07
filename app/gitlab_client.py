import requests
from app import config

_HEADERS = {
    "Authorization": f"Bearer {config.GITLAB_TOKEN}",
    "Content-Type": "application/json",
}


def get_mr(project_id: int, mr_iid: int) -> dict:
    url = f"{config.GITLAB_URL}/api/v4/projects/{project_id}/merge_requests/{mr_iid}"
    resp = requests.get(url, headers=_HEADERS, verify=False)
    resp.raise_for_status()
    return resp.json()


def post_mr_comment(project_id: int, mr_iid: int, body: str) -> None:
    url = f"{config.GITLAB_URL}/api/v4/projects/{project_id}/merge_requests/{mr_iid}/notes"
    resp = requests.post(url, headers=_HEADERS, json={"body": body}, verify=False)
    resp.raise_for_status()


def get_issue(project_id: int, issue_iid: int) -> dict:
    url = f"{config.GITLAB_URL}/api/v4/projects/{project_id}/issues/{issue_iid}"
    resp = requests.get(url, headers=_HEADERS, verify=False)
    resp.raise_for_status()
    return resp.json()


def get_issue_notes(project_id: int, issue_iid: int) -> list:
    url = f"{config.GITLAB_URL}/api/v4/projects/{project_id}/issues/{issue_iid}/notes"
    resp = requests.get(url, headers=_HEADERS, params={"sort": "asc", "per_page": 100}, verify=False)
    resp.raise_for_status()
    return resp.json()
