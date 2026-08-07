import base64
import hmac
import re
import time
import unicodedata
from datetime import datetime
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

import jwt
import requests
import streamlit as st
import streamlit.components.v1 as components


# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title="Branded HTML Publisher",
    page_icon="🚀",
    layout="wide",
)

API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
LONDON_TZ = ZoneInfo("Europe/London")
PUBLISHER_MARKER = "BC Branded HTML Publisher"


# ============================================================
# STREAMLIT SECRETS
# ============================================================

PUBLISH_OWNER = st.secrets["github"]["publish_owner"]
GITHUB_APP_ID = str(st.secrets["github"]["app_id"])
GITHUB_APP_PRIVATE_KEY = st.secrets["github"]["app_private_key"]
ADMIN_DELETE_CODE = st.secrets["app"]["admin_delete_code"]

# Exact approved user list is stored in Streamlit Secrets.
# Email is the login identifier; passcode is private and unique per user.
USERS = {
    str(email).strip().lower(): {
        "name": str(data["name"]).strip(),
        "passcode": str(data["passcode"]),
    }
    for email, data in st.secrets["users"].items()
}

BRANDS = {
    "Action Network": "action-network",
    "Canada Sports Betting": "canada-sports-betting",
    "VegasInsider": "vegasinsider",
    "RotoGrinders": "rotogrinders",
    "USBets": "usbets",
    "SportsHandle": "sportshandle",
}


# ============================================================
# STYLE
# ============================================================

st.markdown(
    """
    <style>
      .block-container {
        max-width: 1180px;
        padding-top: 2rem;
        padding-bottom: 4rem;
      }

      [data-testid="stHeader"] {
        background: transparent;
      }

      .publisher-hero {
        border: 1px solid rgba(128,128,128,.25);
        padding: 26px 28px;
        margin-bottom: 22px;
      }

      .publisher-hero h1 {
        margin: 0 0 8px 0;
        line-height: 1.05;
      }

      .publisher-hero p {
        margin: 0;
        opacity: .72;
      }

      .identity-box {
        border-left: 4px solid #00b67a;
        padding: 12px 14px;
        margin: 10px 0 18px 0;
        background: rgba(0,182,122,.06);
      }

      .repo-box {
        font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
        border: 1px solid rgba(128,128,128,.25);
        padding: 12px 14px;
        overflow-wrap: anywhere;
      }
    </style>
    """,
    unsafe_allow_html=True,
)


# ============================================================
# GENERAL HELPERS
# ============================================================

class GitHubError(RuntimeError):
    pass


def safe_json(response):
    try:
        return response.json()
    except Exception:
        return {}


def github_error_message(response):
    data = safe_json(response)
    message = data.get("message") or response.text or f"HTTP {response.status_code}"
    return f"GitHub API {response.status_code}: {message}"


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-")
    return value or "page"


def repo_name_for(brand_slug: str, page_name: str, when=None) -> str:
    when = when or datetime.now(LONDON_TZ)
    stamp = when.strftime("%d%m%y%H%M")
    page_slug = slugify(page_name)

    fixed_chars = len(brand_slug) + len(stamp) + 2
    max_page_chars = max(8, 96 - fixed_chars)
    page_slug = page_slug[:max_page_chars].strip("-")

    return f"{brand_slug}-{page_slug}-{stamp}"


def basic_html_checks(html_text: str):
    low = html_text.lower()
    return {
        "<!DOCTYPE>": "<!doctype html" in low,
        "<html>": "<html" in low,
        "<head>": "<head" in low,
        "<title>": "<title" in low,
        "<body>": "<body" in low,
    }


# ============================================================
# TEMPORARY EMAIL + PASSCODE LOGIN
# ============================================================

def render_login():
    st.markdown(
        """
        <div class="publisher-hero">
          <h1>Branded HTML Publisher</h1>
          <p>Publish self-contained branded HTML pages to Better Collective GitHub Pages.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.caption(
        "Temporary access: approved Better Collective email + individual passcode. "
        "This can later be replaced by OneLogin SSO."
    )

    with st.form("login_form"):
        email = st.text_input(
            "Better Collective email",
            placeholder="name@bettercollective.com",
        ).strip().lower()

        passcode = st.text_input(
            "Passcode",
            type="password",
        )

        submitted = st.form_submit_button(
            "Sign in",
            type="primary",
            use_container_width=True,
        )

    if submitted:
        user = USERS.get(email)

        if (
            user
            and passcode
            and hmac.compare_digest(
                str(passcode),
                str(user["passcode"]),
            )
        ):
            st.session_state["authenticated_email"] = email
            st.session_state["authenticated_name"] = user["name"]
            st.rerun()

        st.error("Email or passcode is incorrect.")

    st.stop()


if not st.session_state.get("authenticated_email"):
    render_login()


AUTHENTICATED_EMAIL = st.session_state["authenticated_email"]
AUTHENTICATED_NAME = st.session_state["authenticated_name"]


# ============================================================
# GITHUB AUTH
# ============================================================

def github_headers(token: str):
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "bc-branded-html-publisher",
    }


def gh_request(method, path, token, expected=(200,), **kwargs):
    response = requests.request(
        method,
        f"{API_ROOT}{path}",
        headers=github_headers(token),
        timeout=30,
        **kwargs,
    )
    if response.status_code not in expected:
        raise GitHubError(github_error_message(response))
    return response


def build_github_app_jwt():
    now = int(time.time())
    return jwt.encode(
        {
            "iat": now - 60,
            "exp": now + 540,
            "iss": GITHUB_APP_ID,
        },
        GITHUB_APP_PRIVATE_KEY,
        algorithm="RS256",
    )


def create_installation_token():
    app_jwt = build_github_app_jwt()

    app_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {app_jwt}",
        "X-GitHub-Api-Version": API_VERSION,
        "User-Agent": "bc-branded-html-publisher",
    }

    install_response = requests.get(
        f"{API_ROOT}/orgs/{quote(PUBLISH_OWNER)}/installation",
        headers=app_headers,
        timeout=30,
    )
    if install_response.status_code != 200:
        raise GitHubError(github_error_message(install_response))

    installation_id = install_response.json()["id"]

    token_response = requests.post(
        f"{API_ROOT}/app/installations/{installation_id}/access_tokens",
        headers=app_headers,
        timeout=30,
    )
    if token_response.status_code != 201:
        raise GitHubError(github_error_message(token_response))

    data = token_response.json()
    return data["token"], data.get("expires_at")


def get_github_token():
    cached_token = st.session_state.get("_gh_token")
    cached_until = st.session_state.get("_gh_token_valid_until", 0)

    if cached_token and cached_until > time.time() + 300:
        return cached_token

    token, expires_at = create_installation_token()

    valid_until = time.time() + 3000
    if expires_at:
        try:
            valid_until = datetime.fromisoformat(
                expires_at.replace("Z", "+00:00")
            ).timestamp()
        except Exception:
            pass

    st.session_state["_gh_token"] = token
    st.session_state["_gh_token_valid_until"] = valid_until

    return token


# ============================================================
# GITHUB PUBLISH HELPERS
# ============================================================

def repo_exists(token: str, repo_name: str) -> bool:
    response = requests.get(
        f"{API_ROOT}/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}",
        headers=github_headers(token),
        timeout=20,
    )

    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False

    raise GitHubError(github_error_message(response))


def unique_repo_name(token: str, brand_slug: str, page_name: str):
    now = datetime.now(LONDON_TZ)
    candidate = repo_name_for(brand_slug, page_name, now)

    if not repo_exists(token, candidate):
        return candidate

    seconds = now.strftime("%S")
    candidate = f"{candidate[:93]}-{seconds}"

    if not repo_exists(token, candidate):
        return candidate

    for number in range(2, 100):
        suffix = f"-{number}"
        candidate_n = candidate[:100 - len(suffix)] + suffix
        if not repo_exists(token, candidate_n):
            return candidate_n

    raise GitHubError("Unable to create a unique repository name.")


def create_repo(token: str, repo_name: str, brand: str):
    payload = {
        "name": repo_name,
        "description": (
            f"{PUBLISHER_MARKER} | {brand} | "
            f"Published by {AUTHENTICATED_NAME} ({AUTHENTICATED_EMAIL})"
        ),
        "private": False,
        "auto_init": True,
        "has_issues": False,
        "has_projects": False,
        "has_wiki": False,
    }

    return gh_request(
        "POST",
        f"/orgs/{quote(PUBLISH_OWNER)}/repos",
        token,
        expected=(201,),
        json=payload,
    ).json()


def put_new_file(
    token: str,
    repo_name: str,
    branch: str,
    path: str,
    content: bytes,
    message: str,
):
    return gh_request(
        "PUT",
        f"/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}/contents/{quote(path, safe='/')}",
        token,
        expected=(200, 201),
        json={
            "message": message,
            "content": base64.b64encode(content).decode("ascii"),
            "branch": branch,
        },
    ).json()


def get_file(token: str, repo_name: str, path: str):
    response = requests.get(
        f"{API_ROOT}/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}/contents/{quote(path, safe='/')}",
        headers=github_headers(token),
        timeout=20,
    )

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise GitHubError(github_error_message(response))

    return response.json()


def update_file(
    token: str,
    repo_name: str,
    branch: str,
    path: str,
    content: bytes,
    message: str,
):
    current = get_file(token, repo_name, path)

    payload = {
        "message": message,
        "content": base64.b64encode(content).decode("ascii"),
        "branch": branch,
    }

    if current:
        payload["sha"] = current["sha"]

    return gh_request(
        "PUT",
        f"/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}/contents/{quote(path, safe='/')}",
        token,
        expected=(200, 201),
        json=payload,
    ).json()


def enable_pages(token: str, repo_name: str, branch: str):
    endpoint = f"/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}/pages"

    response = requests.post(
        f"{API_ROOT}{endpoint}",
        headers=github_headers(token),
        timeout=30,
        json={
            "build_type": "legacy",
            "source": {
                "branch": branch,
                "path": "/",
            },
        },
    )

    if response.status_code == 201:
        return response.json()

    if response.status_code == 409:
        return gh_request("GET", endpoint, token, expected=(200,)).json()

    raise GitHubError(github_error_message(response))


def get_pages(token: str, repo_name: str):
    response = requests.get(
        f"{API_ROOT}/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}/pages",
        headers=github_headers(token),
        timeout=20,
    )

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise GitHubError(github_error_message(response))

    return response.json()


def list_publisher_repos(token: str):
    repos = []

    for page in range(1, 6):
        batch = gh_request(
            "GET",
            f"/orgs/{quote(PUBLISH_OWNER)}/repos",
            token,
            expected=(200,),
            params={
                "per_page": 100,
                "page": page,
                "type": "all",
                "sort": "created",
                "direction": "desc",
            },
        ).json()

        repos.extend(batch)

        if len(batch) < 100:
            break

    return [
        repo for repo in repos
        if (repo.get("description") or "").startswith(PUBLISHER_MARKER)
    ]


def delete_repo(token: str, repo_name: str):
    gh_request(
        "DELETE",
        f"/repos/{quote(PUBLISH_OWNER)}/{quote(repo_name)}",
        token,
        expected=(204,),
    )


# ============================================================
# HEADER
# ============================================================

st.markdown(
    """
    <div class="publisher-hero">
      <h1>Branded HTML Publisher</h1>
      <p>Paste HTML, choose the brand, publish, and copy the live GitHub Pages URL.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="identity-box">
      <strong>Welcome, {escape(AUTHENTICATED_NAME)}</strong><br>
      {escape(AUTHENTICATED_EMAIL)}
    </div>
    """,
    unsafe_allow_html=True,
)


with st.sidebar:
    st.subheader("Publisher")
    st.caption(f"Signed in as **{AUTHENTICATED_NAME}**")
    st.caption(AUTHENTICATED_EMAIL)
    st.caption(f"Publishing owner: **{PUBLISH_OWNER}**")

    if st.button("Sign out", use_container_width=True):
        for key in list(st.session_state.keys()):
            if key.startswith("_gh_") or key in {
                "authenticated_email",
                "authenticated_name",
                "repo_cache",
            }:
                st.session_state.pop(key, None)
        st.rerun()


publish_tab, manage_tab = st.tabs(["Publish", "Manage Pages"])


# ============================================================
# PUBLISH TAB
# ============================================================

with publish_tab:
    col1, col2 = st.columns(2)

    with col1:
        brand = st.selectbox("Brand", list(BRANDS.keys()))

    with col2:
        page_name = st.text_input(
            "Page name",
            placeholder="e.g. NFL Stadium Family Costs",
        )

    uploaded = st.file_uploader(
        "Optional HTML upload",
        type=["html", "htm"],
    )

    uploaded_html = ""
    if uploaded is not None:
        uploaded_html = uploaded.getvalue().decode("utf-8", errors="replace")

    html_text = st.text_area(
        "Paste HTML",
        value=uploaded_html,
        height=450,
        placeholder="<!DOCTYPE html>\n<html>\n...\n</html>",
    )

    preview_repo = repo_name_for(
        BRANDS[brand],
        page_name or "page-name",
        datetime.now(LONDON_TZ),
    )

    st.markdown("**Repository name preview**")
    st.markdown(
        f'<div class="repo-box">{escape(preview_repo)}</div>',
        unsafe_allow_html=True,
    )
    st.caption(
        "Format: brand-page-name-DDMMYYHHMM using Europe/London time."
    )

    if html_text.strip():
        checks = basic_html_checks(html_text)
        st.caption(
            " · ".join(
                f"{'✓' if passed else '○'} {label}"
                for label, passed in checks.items()
            )
        )

    preview_col, publish_col = st.columns(2)

    with preview_col:
        preview_clicked = st.button(
            "Preview HTML",
            use_container_width=True,
            disabled=not bool(html_text.strip()),
        )

    with publish_col:
        publish_clicked = st.button(
            "Publish to GitHub Pages",
            type="primary",
            use_container_width=True,
            disabled=not bool(page_name.strip() and html_text.strip()),
        )

    if preview_clicked:
        st.markdown("### Preview")
        components.html(html_text, height=760, scrolling=True)

    if publish_clicked:
        created_repo_url = None

        try:
            with st.status("Publishing…", expanded=True) as status:
                st.write("Connecting to GitHub…")
                token = get_github_token()

                st.write("Generating repository name…")
                repo_name = unique_repo_name(
                    token,
                    BRANDS[brand],
                    page_name,
                )

                st.write(f"Creating `{repo_name}`…")
                repo = create_repo(token, repo_name, brand)
                created_repo_url = repo["html_url"]
                branch = repo.get("default_branch") or "main"

                st.write("Uploading `index.html`…")
                put_new_file(
                    token,
                    repo_name,
                    branch,
                    "index.html",
                    html_text.encode("utf-8"),
                    f"Publish HTML page by {AUTHENTICATED_NAME}",
                )

                st.write("Adding `.nojekyll`…")
                put_new_file(
                    token,
                    repo_name,
                    branch,
                    ".nojekyll",
                    b"",
                    "Disable Jekyll processing",
                )

                st.write("Enabling GitHub Pages…")
                pages = enable_pages(token, repo_name, branch)

                pages_url = pages.get("html_url")
                if not pages_url:
                    pages_url = (
                        f"https://{PUBLISH_OWNER.lower()}.github.io/"
                        f"{repo_name}/"
                    )

                status.update(
                    label="Published successfully",
                    state="complete",
                    expanded=False,
                )

            st.success("Repository created and GitHub Pages enabled.")

            st.markdown("#### Live URL")
            st.code(pages_url, language=None)
            st.link_button(
                "Open live page",
                pages_url,
                use_container_width=True,
            )

            st.markdown("#### Repository")
            st.code(created_repo_url, language=None)
            st.link_button(
                "Open GitHub repository",
                created_repo_url,
                use_container_width=True,
            )

            iframe_code = (
                f'<iframe src="{pages_url}" '
                'style="display:block;width:100%;height:9000px;'
                'border:0;overflow:hidden;" '
                f'title="{escape(page_name, quote=True)}"></iframe>'
            )

            st.markdown("#### Ready-to-paste iframe")
            st.code(iframe_code, language="html")

            st.session_state.pop("repo_cache", None)

        except Exception as exc:
            st.error(str(exc))

            if created_repo_url:
                st.warning(
                    "The repository was created before a later publishing step failed."
                )
                st.link_button(
                    "Open partial repository",
                    created_repo_url,
                    use_container_width=True,
                )


# ============================================================
# MANAGE TAB
# ============================================================

with manage_tab:
    st.markdown("### Published Pages")

    if st.button("Refresh list"):
        st.session_state.pop("repo_cache", None)

    try:
        token = get_github_token()

        if "repo_cache" not in st.session_state:
            st.session_state["repo_cache"] = list_publisher_repos(token)

        repos = st.session_state["repo_cache"]

        if not repos:
            st.info("No repositories created by this publisher were found.")
        else:
            repo_names = [repo["name"] for repo in repos]
            selected_name = st.selectbox("Repository", repo_names)
            selected = next(
                repo for repo in repos
                if repo["name"] == selected_name
            )

            pages = get_pages(token, selected_name)
            pages_url = (
                pages.get("html_url")
                if pages
                else f"https://{PUBLISH_OWNER.lower()}.github.io/{selected_name}/"
            )

            open1, open2 = st.columns(2)

            with open1:
                st.link_button(
                    "Open repository",
                    selected["html_url"],
                    use_container_width=True,
                )

            with open2:
                st.link_button(
                    "Open live page",
                    pages_url,
                    use_container_width=True,
                )

            with st.expander("Update index.html"):
                replacement_html = st.text_area(
                    "Replacement HTML",
                    height=320,
                    key=f"replacement_{selected_name}",
                )

                if st.button(
                    "Update existing page",
                    use_container_width=True,
                    disabled=not bool(replacement_html.strip()),
                    key=f"update_{selected_name}",
                ):
                    branch = selected.get("default_branch") or "main"

                    update_file(
                        token,
                        selected_name,
                        branch,
                        "index.html",
                        replacement_html.encode("utf-8"),
                        f"Update HTML by {AUTHENTICATED_NAME}",
                    )

                    st.success(
                        "index.html updated. GitHub Pages will rebuild automatically."
                    )

            with st.expander("Delete repository"):
                st.warning(
                    "This permanently deletes the repository and GitHub Pages site."
                )

                admin_code = st.text_input(
                    "Admin delete code",
                    type="password",
                    key=f"delete_code_{selected_name}",
                )

                confirm_repo = st.text_input(
                    "Type the exact repository name",
                    key=f"delete_confirm_{selected_name}",
                )

                if st.button(
                    "Permanently delete repository",
                    type="primary",
                    use_container_width=True,
                    disabled=not bool(admin_code and confirm_repo == selected_name),
                    key=f"delete_{selected_name}",
                ):
                    if not hmac.compare_digest(
                        str(admin_code),
                        str(ADMIN_DELETE_CODE),
                    ):
                        st.error("Incorrect admin delete code.")
                    else:
                        delete_repo(token, selected_name)
                        st.session_state.pop("repo_cache", None)
                        st.success(f"Deleted {selected_name}.")
                        st.rerun()

    except Exception as exc:
        st.error(str(exc))
