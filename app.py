import base64
import hmac
import re
import time
import unicodedata
from datetime import datetime
from html import escape
from urllib.parse import quote
from zoneinfo import ZoneInfo

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
GITHUB_PAT = st.secrets["github"]["pat"]
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


def get_github_token():
    """
    Publish from the BetterCollective26 personal GitHub account.
    The PAT must be created while signed in as BetterCollective26.
    """
    if not GITHUB_PAT:
        raise GitHubError("GitHub PAT is missing from Streamlit Secrets.")

    response = requests.get(
        f"{API_ROOT}/user",
        headers=github_headers(GITHUB_PAT),
        timeout=20,
    )
    if response.status_code != 200:
        raise GitHubError(github_error_message(response))

    authenticated_login = str(response.json().get("login", "")).lower()

    if authenticated_login != str(PUBLISH_OWNER).lower():
        raise GitHubError(
            f"GitHub PAT belongs to '{authenticated_login}', but publish_owner "
            f"is '{PUBLISH_OWNER}'. Create the PAT while signed in to "
            f"{PUBLISH_OWNER}."
        )

    return GITHUB_PAT


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
        "/user/repos",
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
            "/user/repos",
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
# PUBLISHED PAGE METADATA HELPERS
# ============================================================

def infer_brand_from_repo_name(repo_name: str) -> str:
    for brand_name, brand_slug in BRANDS.items():
        if repo_name.startswith(f"{brand_slug}-"):
            return brand_name
    return "Unknown"


def parse_publisher_description(description: str):
    """
    Expected description:
    BC Branded HTML Publisher | Action Network |
    Published by Gautham (gmarthandan@bettercollective.com)
    """
    description = description or ""

    pattern = (
        rf"^{re.escape(PUBLISHER_MARKER)}\s*\|\s*"
        r"(?P<brand>.*?)\s*\|\s*Published by\s+"
        r"(?P<name>.*?)\s+\((?P<email>[^)]+)\)\s*$"
    )

    match = re.match(pattern, description)

    if not match:
        return {
            "brand": "",
            "creator_name": "Unknown",
            "creator_email": "",
        }

    return {
        "brand": match.group("brand").strip(),
        "creator_name": match.group("name").strip(),
        "creator_email": match.group("email").strip().lower(),
    }


def page_title_from_repo(repo_name: str, brand_name: str) -> str:
    brand_slug = BRANDS.get(brand_name)

    if not brand_slug:
        # Fall back to whichever known brand slug matches the repo.
        for known_brand, known_slug in BRANDS.items():
            if repo_name.startswith(f"{known_slug}-"):
                brand_slug = known_slug
                break

    value = repo_name

    if brand_slug and value.startswith(f"{brand_slug}-"):
        value = value[len(brand_slug) + 1:]

    # Remove DDMMYYHHMM timestamp and optional collision suffix.
    value = re.sub(r"-\d{10}(?:-\d+)?$", "", value)

    return value.replace("-", " ").strip().title() or repo_name


def repo_created_display(repo: dict) -> str:
    raw = repo.get("created_at")

    if not raw:
        return "Unknown"

    try:
        created = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        created_london = created.astimezone(LONDON_TZ)
        return created_london.strftime("%d %b %Y")
    except Exception:
        return raw


def published_page_record(repo: dict) -> dict:
    metadata = parse_publisher_description(repo.get("description") or "")

    brand = metadata["brand"] or infer_brand_from_repo_name(repo["name"])
    creator_name = metadata["creator_name"]
    creator_email = metadata["creator_email"]

    return {
        "repo": repo,
        "repo_name": repo["name"],
        "page": page_title_from_repo(repo["name"], brand),
        "brand": brand,
        "creator_name": creator_name,
        "creator_email": creator_email,
        "published": repo_created_display(repo),
        "repo_url": repo["html_url"],
        "live_url": (
            f"https://{PUBLISH_OWNER.lower()}.github.io/"
            f"{repo['name']}/"
        ),
    }


def render_published_pages_table(records):
    rows = []

    for record in records:
        rows.append(
            f"""
            <tr>
              <td class="page-name">{escape(record["page"])}</td>
              <td>{escape(record["brand"])}</td>
              <td>{escape(record["creator_name"])}</td>
              <td>{escape(record["published"])}</td>
              <td><a href="{escape(record["live_url"], quote=True)}" target="_blank" rel="noopener noreferrer">Open</a></td>
              <td><a href="{escape(record["repo_url"], quote=True)}" target="_blank" rel="noopener noreferrer">GitHub</a></td>
            </tr>
            """
        )

    table_html = f"""
    <style>
      .published-table-wrap {{
        width: 100%;
        overflow-x: auto;
        border: 1px solid rgba(128,128,128,.24);
        margin: 10px 0 22px 0;
      }}

      table.published-table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 0.94rem;
      }}

      .published-table th {{
        text-align: left;
        font-weight: 600;
        padding: 12px 14px;
        border-bottom: 1px solid rgba(128,128,128,.28);
        background: rgba(128,128,128,.07);
        white-space: nowrap;
      }}

      .published-table td {{
        padding: 13px 14px;
        border-bottom: 1px solid rgba(128,128,128,.16);
        vertical-align: middle;
      }}

      .published-table tr:last-child td {{
        border-bottom: 0;
      }}

      .published-table .page-name {{
        font-weight: 600;
        min-width: 220px;
      }}

      .published-table a {{
        text-decoration: none;
        font-weight: 600;
      }}
    </style>

    <div class="published-table-wrap">
      <table class="published-table">
        <thead>
          <tr>
            <th>Page</th>
            <th>Brand</th>
            <th>Created By</th>
            <th>Published</th>
            <th>Live Page</th>
            <th>Repository</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
    """

    st.markdown(table_html, unsafe_allow_html=True)


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
                st.write(f"Connecting to GitHub as {PUBLISH_OWNER}…")
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
            st.caption(
                "GitHub Pages may take a minute to go live. "
                "If it isn’t ready yet, check the link again shortly."
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
    st.caption("Live pages are open to all approved users. Repo, Replace and Delete are creator-only.")

    try:
        token = get_github_token()
        repos = list_publisher_repos(token)

        if not repos:
            st.info("No published pages yet.")
        else:
            records = [published_page_record(repo) for repo in repos]

            # ------------------------------------------------------------
            # Compact native Streamlit table
            # ------------------------------------------------------------

            # Tighten button/row spacing for the table-like layout.
            st.markdown(
                """
                <style>
                  .compact-table-head {
                    font-size: .78rem;
                    font-weight: 600;
                    opacity: .72;
                    padding: 0 2px 5px 2px;
                  }

                  .compact-cell {
                    font-size: .83rem;
                    line-height: 1.2;
                    padding-top: .48rem;
                    white-space: nowrap;
                    overflow: hidden;
                    text-overflow: ellipsis;
                  }

                  div[data-testid="stHorizontalBlock"] {
                    gap: .35rem;
                  }

                  div[data-testid="stButton"] > button,
                  div[data-testid="stLinkButton"] > a {
                    min-height: 2rem;
                    padding: .28rem .45rem;
                    font-size: .78rem;
                  }
                </style>
                """,
                unsafe_allow_html=True,
            )

            widths = [2.25, 1.6, .9, 1.08, .72, .72, .82, .72]

            header = st.columns(widths)
            for col, label in zip(
                header,
                ["Page", "Brand", "By", "Published", "Live", "Repo", "Replace", "Delete"],
            ):
                with col:
                    st.markdown(
                        f'<div class="compact-table-head">{label}</div>',
                        unsafe_allow_html=True,
                    )

            st.divider()

            for idx, record in enumerate(records):
                row = st.columns(widths)

                with row[0]:
                    st.markdown(
                        f'<div class="compact-cell"><strong>{escape(record["page"])}</strong></div>',
                        unsafe_allow_html=True,
                    )

                with row[1]:
                    st.markdown(
                        f'<div class="compact-cell">{escape(record["brand"])}</div>',
                        unsafe_allow_html=True,
                    )

                with row[2]:
                    st.markdown(
                        f'<div class="compact-cell">{escape(record["creator_name"])}</div>',
                        unsafe_allow_html=True,
                    )

                with row[3]:
                    st.markdown(
                        f'<div class="compact-cell">{escape(record["published"])}</div>',
                        unsafe_allow_html=True,
                    )

                with row[4]:
                    st.link_button(
                        "Open",
                        record["live_url"],
                        use_container_width=True,
                    )

                with row[5]:
                    if st.button(
                        "Repo",
                        key=f"repo_btn_{record['repo_name']}_{idx}",
                        use_container_width=True,
                    ):
                        st.session_state["pending_manage_action"] = "repo"
                        st.session_state["pending_manage_repo"] = record["repo_name"]
                        st.session_state["manage_action_open"] = True
                        st.session_state.pop("manage_reauthed", None)
                        st.rerun()

                with row[6]:
                    if st.button(
                        "Replace",
                        key=f"replace_btn_{record['repo_name']}_{idx}",
                        use_container_width=True,
                    ):
                        st.session_state["pending_manage_action"] = "replace"
                        st.session_state["pending_manage_repo"] = record["repo_name"]
                        st.session_state["manage_action_open"] = True
                        st.session_state.pop("manage_reauthed", None)
                        st.rerun()

                with row[7]:
                    if st.button(
                        "Delete",
                        key=f"delete_btn_{record['repo_name']}_{idx}",
                        use_container_width=True,
                    ):
                        st.session_state["pending_manage_action"] = "delete"
                        st.session_state["pending_manage_repo"] = record["repo_name"]
                        st.session_state["manage_action_open"] = True
                        st.session_state.pop("manage_reauthed", None)
                        st.rerun()

                st.markdown(
                    '<div style="height:1px;background:rgba(128,128,128,.12);margin:.15rem 0 .25rem 0;"></div>',
                    unsafe_allow_html=True,
                )

            # ------------------------------------------------------------
            # Creator-only action panel
            # ------------------------------------------------------------

            action = st.session_state.get("pending_manage_action")
            selected_repo_name = st.session_state.get("pending_manage_repo")

            selected_record = next(
                (
                    record
                    for record in records
                    if record["repo_name"] == selected_repo_name
                ),
                None,
            )

            if (
                st.session_state.get("manage_action_open")
                and action in {"repo", "replace", "delete"}
                and selected_record
            ):
                creator_email = (
                    selected_record.get("creator_email") or ""
                ).strip().lower()

                # Older publisher-created repos may not include creator email.
                # Resolve it by unique first-name match when possible.
                if not creator_email:
                    matching_emails = [
                        email
                        for email, user_data in USERS.items()
                        if str(user_data["name"]).strip().lower()
                        == str(selected_record["creator_name"]).strip().lower()
                    ]
                    if len(matching_emails) == 1:
                        creator_email = matching_emails[0]

                is_owner = (
                    bool(creator_email)
                    and AUTHENTICATED_EMAIL == creator_email
                )

                st.divider()

                if not is_owner:
                    creator_name = selected_record["creator_name"] or "the original creator"

                    st.info(
                        f"Created by {creator_name}. "
                        f"Sign in as {creator_name} to access the repo or make changes."
                    )

                    if st.button(
                        "Close",
                        use_container_width=True,
                        key="close_not_owner",
                    ):
                        st.session_state.pop("pending_manage_action", None)
                        st.session_state.pop("pending_manage_repo", None)
                        st.session_state.pop("manage_reauthed", None)
                        st.session_state.pop("manage_action_open", None)
                        st.rerun()

                else:
                    action_labels = {
                        "repo": "Open Repository",
                        "replace": "Replace HTML",
                        "delete": "Delete Page",
                    }

                    st.markdown(
                        f"#### {action_labels[action]} · {selected_record['page']}"
                    )
                    st.caption(
                        f"{selected_record['brand']} · "
                        f"{selected_record['creator_name']} · "
                        f"{selected_record['published']}"
                    )

                    # Require fresh creator passcode for every action click.
                    if not st.session_state.get("manage_reauthed"):
                        with st.form(
                            f"manage_reauth_{selected_repo_name}_{action}",
                            clear_on_submit=True,
                        ):
                            passcode = st.text_input(
                                "Re-enter your passcode",
                                type="password",
                            )

                            confirm = st.form_submit_button(
                                "Continue",
                                type="primary",
                                use_container_width=True,
                            )

                        if confirm:
                            expected = str(
                                USERS[AUTHENTICATED_EMAIL]["passcode"]
                            )

                            if (
                                passcode
                                and hmac.compare_digest(
                                    str(passcode),
                                    expected,
                                )
                            ):
                                st.session_state["manage_reauthed"] = True
                                st.rerun()
                            else:
                                st.error("Incorrect passcode.")

                    else:
                        selected = selected_record["repo"]

                        if action == "repo":
                            st.success("Identity confirmed.")
                            st.link_button(
                                "Open GitHub repository",
                                selected_record["repo_url"],
                                use_container_width=True,
                            )
                            st.caption(
                                "The repository contains the published index.html."
                            )

                            if st.button(
                                "Done",
                                use_container_width=True,
                                key="done_repo_action",
                            ):
                                st.session_state.pop("pending_manage_action", None)
                                st.session_state.pop("pending_manage_repo", None)
                                st.session_state.pop("manage_reauthed", None)
                                st.session_state.pop("manage_action_open", None)
                                st.rerun()

                        elif action == "replace":
                            replacement_html = st.text_area(
                                "New HTML",
                                height=300,
                                placeholder="Paste the complete replacement HTML here.",
                                key=f"replacement_{selected_repo_name}",
                            )

                            update_col, cancel_col = st.columns(2)

                            with update_col:
                                if st.button(
                                    "Update page",
                                    type="primary",
                                    use_container_width=True,
                                    disabled=not bool(replacement_html.strip()),
                                    key="confirm_replace_action",
                                ):
                                    branch = (
                                        selected.get("default_branch")
                                        or "main"
                                    )

                                    update_file(
                                        token,
                                        selected_repo_name,
                                        branch,
                                        "index.html",
                                        replacement_html.encode("utf-8"),
                                        f"Update HTML by {AUTHENTICATED_NAME}",
                                    )

                                    st.session_state.pop("pending_manage_action", None)
                                    st.session_state.pop("pending_manage_repo", None)
                                    st.session_state.pop("manage_reauthed", None)
                                    st.session_state.pop("manage_action_open", None)
                                    st.success("Page updated.")
                                    st.rerun()

                            with cancel_col:
                                if st.button(
                                    "Cancel",
                                    use_container_width=True,
                                    key="cancel_replace_action",
                                ):
                                    st.session_state.pop("pending_manage_action", None)
                                    st.session_state.pop("pending_manage_repo", None)
                                    st.session_state.pop("manage_reauthed", None)
                                    st.rerun()

                        elif action == "delete":
                            st.warning(
                                "This permanently deletes the repository and live page."
                            )

                            delete_col, cancel_col = st.columns(2)

                            with delete_col:
                                if st.button(
                                    "Delete permanently",
                                    type="primary",
                                    use_container_width=True,
                                    key="confirm_delete_action",
                                ):
                                    delete_repo(token, selected_repo_name)

                                    st.session_state.pop("pending_manage_action", None)
                                    st.session_state.pop("pending_manage_repo", None)
                                    st.session_state.pop("manage_reauthed", None)
                                    st.session_state.pop("manage_action_open", None)

                                    st.success("Page deleted.")
                                    st.rerun()

                            with cancel_col:
                                if st.button(
                                    "Cancel",
                                    use_container_width=True,
                                    key="cancel_delete_action",
                                ):
                                    st.session_state.pop("pending_manage_action", None)
                                    st.session_state.pop("pending_manage_repo", None)
                                    st.session_state.pop("manage_reauthed", None)
                                    st.rerun()

    except Exception as exc:
        st.error(str(exc))
