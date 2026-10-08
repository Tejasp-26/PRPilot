"""
app_streamlit.py — Minimalist, professional Web UI for PRPilot built with Streamlit.

Run with:  streamlit run app_streamlit.py

Allows users to paste a GitHub PR URL, run the review graph, display a clean
findings dashboard with structured stats, and link directly to the posted PR comments.
No emojis, clean corporate theme, high contrast readability.

SECURITY: finding text comes from the LLM and from PR content (attacker
controlled), so every value interpolated into raw HTML is html-escaped.
"""

from __future__ import annotations

import html

import streamlit as st

# Set page config
st.set_page_config(
    page_title="PRPilot - GitHub PR Reviewer",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from prpilot.cli import parse_pr_url, run


# ──────────────────────────────────────────────
# CSS Injector for Clean, Professional Aesthetics
# ──────────────────────────────────────────────

st.markdown(
    """
    <style>
    /* Import Plus Jakarta Sans and Inter */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');

    /* Font applications */
    html, body, [class*="css"]  {
        font-family: 'Inter', sans-serif;
    }
    h1, h2, h3, h4, h5, h6 {
        font-family: 'Plus Jakarta Sans', sans-serif;
        font-weight: 600;
        letter-spacing: -0.02em;
    }

    /* Professional Title Styling */
    .title-main {
        color: #F8F9FA;
        font-size: 2.8rem;
        font-weight: 700;
        margin-bottom: 0.1rem;
    }
    
    .title-sub {
        color: #6C757D;
        font-size: 1.1rem;
        font-weight: 400;
        margin-bottom: 2.5rem;
    }

    /* Clean Card Styling */
    .card-finding {
        border-radius: 8px;
        padding: 1.2rem;
        margin-bottom: 1.2rem;
        border-left: 4px solid;
    }
    
    .card-critical {
        background-color: #1A1012;
        border-color: #DC3545;
        border-top: 1px solid #2D181C;
        border-right: 1px solid #2D181C;
        border-bottom: 1px solid #2D181C;
    }

    .card-high {
        background-color: #1A1310;
        border-color: #FD7E14;
        border-top: 1px solid #2D201A;
        border-right: 1px solid #2D201A;
        border-bottom: 1px solid #2D201A;
    }

    .card-medium {
        background-color: #1A1910;
        border-color: #FFC107;
        border-top: 1px solid #2D2A1A;
        border-right: 1px solid #2D2A1A;
        border-bottom: 1px solid #2D2A1A;
    }

    .card-low {
        background-color: #10151A;
        border-color: #0D6EFD;
        border-top: 1px solid #1A242D;
        border-right: 1px solid #1A242D;
        border-bottom: 1px solid #1A242D;
    }

    /* Action Banner */
    .action-banner {
        background-color: #151419;
        border: 1px solid #2F2E35;
        border-radius: 12px;
        padding: 1.8rem;
        text-align: center;
        margin-bottom: 2rem;
    }

    /* Category badges */
    .badge {
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-size: 0.75rem;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        margin-right: 0.5rem;
    }
    
    .badge-sec { background-color: rgba(220, 53, 69, 0.15); color: #E4606D; }
    .badge-perf { background-color: rgba(13, 110, 253, 0.15); color: #4DA3FF; }
    .badge-style { background-color: rgba(255, 193, 7, 0.15); color: #FFD54F; }
    .badge-cov { background-color: rgba(111, 66, 193, 0.15); color: #A885E6; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ──────────────────────────────────────────────
# Main Application Layout
# ──────────────────────────────────────────────

# Header Title
st.markdown('<div class="title-main">PRPilot</div>', unsafe_allow_html=True)
st.markdown('<div class="title-sub">Multi-Agent GitHub Pull Request Reviewer</div>', unsafe_allow_html=True)

# PR Input section
st.subheader("Submit Pull Request for Review")
pr_url_input = st.text_input(
    "GitHub PR URL",
    placeholder="https://github.com/owner/repo/pull/1",
    label_visibility="collapsed"
)

# Button trigger
if st.button("Run Code Review", use_container_width=True, type="primary"):
    if not pr_url_input:
        st.error("Please enter a valid GitHub Pull Request URL.")
    else:
        try:
            # Pre-validate URL
            owner, repo, pr_number = parse_pr_url(pr_url_input)
            
            # Show progress
            with st.status("Analyzing diffs, linters, security configurations, and performance heuristics...", expanded=True) as status:
                st.write("Fetching repository metadata and changed files...")
                final_state = run(pr_url_input)
                status.update(label="Review complete.", state="complete", expanded=False)

            review_url = final_state.get("posted_review_url")

            if final_state.get("dry_run"):
                st.warning(
                    "Dry run: GITHUB_TOKEN is not set, so the review was NOT posted to GitHub. "
                    "The findings below are what would have been posted."
                )
            elif review_url:
                st.success("Review posted to GitHub.")
                st.markdown(
                    """
                    <div class="action-banner">
                        <h3 style="margin-top: 0; color: #F8F9FA;">Review Comments Published</h3>
                        <p style="font-size: 1rem; color: #ADB5BD; margin-bottom: 1.2rem;">
                            The review has been compiled, priority-ranked, and posted to your pull request.
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
                st.link_button(
                    "View Comments on GitHub",
                    review_url,
                    use_container_width=True,
                )
            else:
                st.error("The review was generated but posting to GitHub failed. See the logs for details.")

            # Summary dashboard
            st.divider()
            st.markdown("## Review Dashboard")
            
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.markdown("### Executive Summary")
                st.markdown(final_state.get("review_summary", "N/A"))
            
            with col2:
                st.markdown("### Language Coverage & Tools")
                coverage = final_state.get("language_coverage_summary", "No coverage information.")
                st.info(coverage)
            
            # Detailed Findings
            st.markdown("### Detailed Findings")
            all_findings = final_state.get("all_findings", [])
            
            if not all_findings:
                st.success("All checks passed. No issues found.")
            else:
                for idx, f in enumerate(all_findings, 1):
                    # Define layout class based on severity
                    if f.severity == "critical":
                        card_class = "card-critical"
                        sev_text = "CRITICAL"
                    elif f.severity == "high":
                        card_class = "card-high"
                        sev_text = "HIGH"
                    elif f.severity == "medium":
                        card_class = "card-medium"
                        sev_text = "MEDIUM"
                    elif f.severity == "low":
                        card_class = "card-low"
                        sev_text = "LOW"
                    else:
                        card_class = "card-low"
                        sev_text = "INFO"

                    # Select category badge
                    cat_map = {
                        "security": '<span class="badge badge-sec">Security</span>',
                        "performance": '<span class="badge badge-perf">Performance</span>',
                        "style": '<span class="badge badge-style">Style/Lint</span>',
                        "test_coverage": '<span class="badge badge-cov">Test Coverage</span>',
                    }
                    cat_badge = cat_map.get(f.category, f.category)

                    line_str = f"Line {f.line}" if f.line else "Whole File"
                    fallback_text = " (LLM Fallback)" if f.used_fallback else ""
                    file_html = html.escape(f.file)
                    source_html = html.escape(f.source_tool)
                    message_html = html.escape(f.message).replace("\n", "<br>")
                    fix_html = html.escape(f.suggested_fix).replace("\n", "<br>") if f.suggested_fix else ""

                    st.markdown(
                        f"""
                        <div class="card-finding {card_class}">
                            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.5rem;">
                                <strong style="font-size: 1rem; color: #F8F9FA;">{idx}. {sev_text} - {file_html} ({line_str})</strong>
                                <div>
                                    {cat_badge}
                                    <span style="font-size: 0.75rem; color: #6C757D; margin-left: 0.5rem;">Source: {source_html}{fallback_text}</span>
                                </div>
                            </div>
                            <p style="margin-top: 0.4rem; color: #E9ECEF; font-size: 0.9rem; line-height: 1.4;">{message_html}</p>
                            {f'<div style="background-color: #111; padding: 0.8rem; border-radius: 6px; font-family: monospace; font-size: 0.85rem; color: #98C379; margin-top: 0.5rem; border: 1px solid #222;"><strong style="color: #61AFEF;">Suggested Fix:</strong><br>{fix_html}</div>' if fix_html else ''}
                        </div>
                        """,
                        unsafe_allow_html=True
                    )
                    
        except ValueError as e:
            st.error(f"Invalid PR URL format: {e}")
        except Exception as e:
            st.error(f"An error occurred during execution: {e}")
            st.exception(e)
