"""Step 10.3 — the playground: a paste box in front of the deployed agent.

    uv run --group ui streamlit run ui/app.py

`s2p.fly.dev` is a JSON API. Everything the last three phases built — the four
gates, the reflection loop, the critic, the report — is real and *invisible*: a
person who opens that URL sees `{"detail":"Not Found"}`. This page is the
difference between "there is an agent deployed" and "here, try it".

It holds no logic. Streamlit re-runs this whole file top to bottom on every
click, which makes it a fine place for a layout and a terrible place for
anything you would want to test; the conversion, the scorecard, the limits and
the feedback all live in `selenium2playwright/playground.py`, which is ordinary
Python with ordinary tests. This file decides where things go on the screen.

Two things it does not do, both deliberate:

**It does not hand out a key.** `S2P_DEMO_KEY` is read from the environment of
the machine running Streamlit and never reaches the browser. A visitor gets a
URL, not a credential.

**It does not ask the graph anything a stranger may not ask.** No server paths,
no writes to shared memory, no suite runs — `guard.py` refuses all of that
anyway, and the playground simply never offers it.
"""

from __future__ import annotations

import streamlit as st

from selenium2playwright import playground as pg

st.set_page_config(
    page_title="Selenium → Playwright",
    page_icon="🎭",
    layout="wide",
    initial_sidebar_state="expanded",
)

# --- session ------------------------------------------------------------------
# Streamlit keeps `st.session_state` across re-runs of this file for one browser
# tab, which is the only memory this page has. The visitor id lives here because
# the per-visitor limits are supposed to count *people*, and every visitor
# shares this server's IP address.

DEFAULTS = {
    "visitor": None,
    "thread_id": None,
    "source": "",
    "filename": "",
    "companion_name": "",
    "companion_source": "",
    "outcome": None,      # (Scorecard, submitted source, submitted name)
    "run_id": "",
    "trail": [],          # the node lines from the last run
    "feedback": "",
    "error": "",
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value() if callable(value) else value
if st.session_state.visitor is None:
    st.session_state.visitor = pg.new_visitor()


def load_sample(sample: pg.Sample) -> None:
    """Fill the box from a real file in the repository. Runs before the re-run.

    A widget's value cannot be assigned after the widget has been drawn, so this
    is an `on_click` callback: Streamlit runs it, *then* re-runs the script, and
    the text area is built from the new state.
    """
    st.session_state.source = sample.read()
    st.session_state.filename = sample.name
    # A spec's page object, already converted, shown in the box rather than
    # smuggled in: the visitor can see the whole request, and can edit or
    # delete it. `Sample.context()` explains why a spec needs one at all.
    companion = sample.context()
    st.session_state.companion_name = next(iter(companion), "")
    st.session_state.companion_source = next(iter(companion.values()), "")
    st.session_state.outcome = None
    st.session_state.error = ""


def convert(source: str, name: str, *, context: dict[str, str], thread_id: str | None,
            area, refinement: str = "") -> None:
    """Run one conversion and put the result in session state.

    `thread_id=None` means a fresh conversion: the server makes a new thread, so
    nothing from the previous file leaks into this one. Passing an existing
    thread is a *refine* — turn two of the same conversation, where the graph
    still has the last draft and every instruction given so far (step 7.1).

    `area` is a container to draw the progress into. Streamlit writes wherever
    the code happens to be running, and a refine is triggered from a button
    buried under the result — so without this the live progress of a minute-long
    run appears somewhere below the fold while the top of the page still shows
    the previous answer, looking like nothing is happening.
    """
    st.session_state.error = ""
    st.session_state.feedback = ""
    client = pg.client(visitor=st.session_state.visitor)
    seen: list[str] = []

    try:
        if thread_id is None:
            thread_id = client.threads.create()["thread_id"]
            st.session_state.thread_id = thread_id

        request = pg.payload(source, name, context=context, refinement=refinement)
        final: dict = {}
        with area, st.status("Converting…", expanded=True) as status:
            for update in pg.stream(client, thread_id, request):
                if update.kind == "run":
                    st.session_state.run_id = update.run_id
                elif update.kind == "node":
                    seen.append(update.node)
                    st.write(pg.progress_label(update.node, seen))
                elif update.kind == "state":
                    final = update.state
            card = pg.scorecard(final)
            status.update(label=f"Finished — {card.status}", state="complete", expanded=False)
    except Exception as exc:  # noqa: BLE001 — every failure here is somebody else's server
        wait = pg.retry_after(exc)
        st.session_state.error = pg.explain(exc) + (
            f" You can try again in about {wait}s." if wait else ""
        )
        return

    st.session_state.trail = seen
    st.session_state.outcome = (card, source, name)


# --- sidebar: what this is, and what it costs ---------------------------------

with st.sidebar:
    st.markdown("### Selenium → Playwright")
    st.caption(
        "A LangGraph agent that converts a TypeScript Selenium file to "
        "Playwright, compiles what it wrote, and reviews it — up to three times."
    )

    st.markdown("**Backend**")
    st.code(pg.backend_url(), language=None)

    if not pg.demo_key():
        st.error(
            "No `S2P_DEMO_KEY` in this app's environment, so every request will "
            "be refused. Set it to the deployment's demo key (deploy.sh "
            "generates one), or run the backend with `S2P_AUTH=off`."
        )

    limits = pg.fetch_limits(visitor=st.session_state.visitor)
    if "error" in limits:
        st.warning(limits["error"])
    else:
        budget = limits.get("budget") or {}
        used, cap = budget.get("used", 0), budget.get("limit", 1) or 1
        st.progress(min(1.0, used / cap), text=pg.budget_line(limits))
        st.caption(pg.visitor_line(limits))
    st.button("Refresh budget", width="stretch")

    with st.expander("What happens when you press Convert"):
        st.markdown(
            """
1. **Intake** — reads the file and classifies it: page object, spec, or something
   this tool should refuse.
2. **Recall** — looks in long-term memory for conventions worth applying.
3. **Convert** — one model writes the Playwright version.
4. **Validate** — four deterministic gates run against the *written* file:
   `tsc` compile, a residue scan for leftover Selenium, ESLint, and a parity
   check that no test quietly disappeared.
5. **Critic** — a second model reads the result and the gate findings.
6. If anything failed, it goes round again with those findings in hand —
   **at most three attempts**, then it reports honestly either way.

`needs-review` is a real outcome, not a failure: it means the code compiles and
something still needs your eyes, and the TODOs say what.
            """
        )
    st.caption(
        "Demo limits are per visitor and per day, and the daily budget is shared "
        "by everyone. The key stays on this server."
    )

# --- the samples --------------------------------------------------------------

st.title("Convert a Selenium file to Playwright")
st.caption(
    "Paste a TypeScript Selenium file, or start from one of these. Every result "
    "below was compiled by a real TypeScript compiler before you saw it."
)

catalogue = pg.samples()
if catalogue:
    for column, sample in zip(st.columns(len(catalogue)), catalogue):
        column.button(
            sample.name,
            help=sample.blurb,
            width="stretch",
            on_click=load_sample,
            args=(sample,),
        )

left, right = st.columns(2, gap="large")

# --- input --------------------------------------------------------------------

with left:
    st.text_input(
        "File name",
        key="filename",
        placeholder="LoginPage.ts",
        help="A label only. The demo never opens a file on the server, so this "
             "may not be a path.",
    )
    st.text_area(
        "Selenium (TypeScript)",
        key="source",
        height=440,
        placeholder="import { By, until, WebDriver } from 'selenium-webdriver';",
    )

    # A spec imports its page object, and the server has neither file until you
    # send them. Converting a spec alone therefore fails `tsc` on an import that
    # cannot resolve — a true finding about the paste box rather than about the
    # conversion. This is the way out, and it is the same input the suite graph
    # gives every file in wave 2.
    with st.expander(
        "Companion file (optional)"
        + (f" — sending {st.session_state.companion_name}"
           if st.session_state.companion_source.strip() else "")
    ):
        st.caption(
            "If this file imports another one, paste the **already-converted** "
            "Playwright version here so the compiler can see it."
        )
        st.text_input("Companion name", key="companion_name", placeholder="LoginPage.ts")
        st.text_area("Companion (Playwright)", key="companion_source", height=160)

    complaint = pg.check_input(
        st.session_state.source,
        st.session_state.filename,
        companion_name=st.session_state.companion_name,
        companion_text=st.session_state.companion_source,
    )
    go = st.button(
        "Convert",
        type="primary",
        width="stretch",
        disabled=bool(complaint),
    )
    if complaint:
        st.caption(complaint)

context = (
    {st.session_state.companion_name: st.session_state.companion_source}
    if st.session_state.companion_source.strip() and st.session_state.companion_name.strip()
    else {}
)

# --- output -------------------------------------------------------------------

with right:
    # Claimed before anything is drawn, so a run that starts later — a refine,
    # from a button below the result — still reports at the top of the column.
    progress = st.container()

    if go:
        convert(st.session_state.source, st.session_state.filename,
                context=context, thread_id=None, area=progress)
        # Re-run once the conversion is over. The sidebar is drawn before the
        # run starts, so without this the budget line still shows the number
        # from before the conversion that just spent one of them — a small lie
        # in the one place on the page whose whole job is to be accurate.
        st.rerun()

    if st.session_state.error:
        st.error(st.session_state.error)

    if st.session_state.outcome is None:
        if not st.session_state.error:
            st.info(
                "The result appears here: the converted file, a diff against "
                "what you pasted, and the scorecard from the four gates and the "
                "critic."
            )
    else:
        card, submitted, submitted_name = st.session_state.outcome

        if card.status == "refused":
            st.warning(f"**Refused.** {card.reason}")
        elif card.passed:
            st.success(f"**Passed** after {card.attempts} attempt(s). {card.reason}")
        else:
            st.warning(f"**{card.status}** after {card.attempts} attempt(s). {card.reason}")

        if st.session_state.trail:
            # The live status box belongs to the run that produced it and is
            # gone after the re-run above. The trail outlives it, because "it
            # went round three times" is the most interesting thing about a
            # conversion and should not vanish the moment it finishes.
            with st.expander(f"What it did — {len(st.session_state.trail)} steps"):
                for index, node in enumerate(st.session_state.trail, start=1):
                    st.markdown(f"{index}. {pg.progress_label(node, st.session_state.trail[:index])}")

        if card.gates:
            for column, (gate, ok) in zip(st.columns(len(card.gates)), card.gates):
                column.metric(gate, "PASS" if ok else "FAIL")
        critic_column, model_column = st.columns(2)
        critic_column.caption(f"**Critic:** {card.critic}")
        model_column.caption(
            "**Models:** " + ", ".join(f"{role} `{name}`" for role, name in card.models.items())
        )

        if card.code:
            code_tab, diff_tab, review_tab = st.tabs(["Playwright", "Diff", "Review"])
            with code_tab:
                st.code(card.code, language="typescript", line_numbers=True)
                st.download_button(
                    "Download",
                    card.code,
                    file_name=pg.download_name(submitted_name),
                    mime="text/plain",
                )
            with diff_tab:
                st.code(
                    pg.unified_diff(submitted, card.code,
                                    submitted_name or "selenium.ts",
                                    pg.download_name(submitted_name)),
                    language="diff",
                )
            with review_tab:
                if card.todos:
                    st.markdown("**TODO(review) — every one of them, in one place**")
                    for todo in card.todos:
                        st.markdown(f"- {todo}")
                else:
                    st.markdown("**No TODO(review) items.**")
                if card.notes:
                    st.markdown("**Notes from the conversion**")
                    for note in card.notes:
                        st.markdown(f"- {note}")
                if card.errors:
                    st.markdown("**Errors recorded during the run**")
                    for error in card.errors:
                        st.markdown(f"- {error}")

            with st.expander("Ask for a change"):
                st.caption(
                    "A second turn on the same thread: the agent still has this "
                    "draft and everything you have told it so far."
                )
                # A form, not a bare text box and button. Streamlit only commits
                # a text input's value when it loses focus or the user presses
                # Enter, so typing an instruction and clicking straight on the
                # button sent the *previous* value — nothing, the first time.
                # Inside a form the two widgets submit together, always.
                with st.form("refine", border=False):
                    instruction = st.text_input(
                        "In plain English",
                        placeholder="Use getByRole for the buttons.",
                    )
                    asked = st.form_submit_button("Refine")
                if asked and instruction.strip():
                    convert(submitted, submitted_name, context=context,
                            thread_id=st.session_state.thread_id,
                            area=progress, refinement=instruction.strip())
                    st.rerun()
                elif asked:
                    st.caption("Say what you would like changed.")

        # --- the flywheel ---------------------------------------------------
        # A 👍 is a number. A 👎 sends the file back with it, which is what
        # `feedback.py` queues into the dataset — the input somebody says we got
        # wrong is the only thing here worth training against.
        st.divider()
        st.caption("Was this a good conversion?")
        # Also a form, and here it is not a nicety: the comment box and the
        # buttons are separate widgets, so a comment typed and then clicked away
        # from would have been dropped silently — which is the one piece of
        # feedback with any detail in it.
        with st.form("verdict", border=False):
            comment = st.text_input(
                "Anything to add? (optional)", label_visibility="collapsed",
                placeholder="What was wrong with it?",
            )
            up, down, _ = st.columns([1, 1, 6])
            said = {
                1.0: up.form_submit_button("👍", width="stretch",
                                           disabled=not st.session_state.run_id),
                0.0: down.form_submit_button("👎", width="stretch",
                                             disabled=not st.session_state.run_id),
            }
        for score, clicked in said.items():
            if clicked:
                answer = pg.send_feedback(
                    st.session_state.run_id, score,
                    comment=comment,
                    source_text=submitted,
                    source_path=submitted_name,
                    visitor=st.session_state.visitor,
                )
                st.session_state.feedback = answer.get("detail") or (
                    "Thank you." if answer.get("stored") else "Not recorded."
                )
        if st.session_state.feedback:
            st.caption(st.session_state.feedback)

st.divider()
st.caption(
    "Built with LangGraph. The agent, the four gates and this page are all in "
    "[github.com/varunbhatt2193/selenium2playwright]"
    "(https://github.com/varunbhatt2193/selenium2playwright)."
)
