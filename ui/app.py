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

from pathlib import Path

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
    # --- the whole-suite tab. Paths rather than text, because that tab only
    # exists when the backend is this machine.
    "suite_root": "samples/selenium-suite",
    "suite_out": "out/playground-suite",
    "suite_only": "",
    "suite_parallel": 4,
    "suite_attempts": 3,
    "suite_model": "",
    # Off, where the CLI defaults it on. A first suite run should have the
    # fewest moving parts: recall needs the server's embeddings configured, and
    # a page that fails on a store it never mentioned is a bad first answer.
    "suite_recall": False,
    "suite_outcome": None,   # a pg.SuiteResult
    "suite_error": "",
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value() if callable(value) else value
if st.session_state.visitor is None:
    st.session_state.visitor = pg.new_visitor()


def take_upload() -> None:
    """Move an uploaded file into the paste box. Runs before the re-run.

    Deliberately fills the *visible* text area rather than converting straight
    from the bytes. The whole argument of this page is that you can see exactly
    what is being sent, and a file that vanished into a request would be a
    worse paste box, not a better one. It is also editable afterwards, which is
    how somebody trims a 300-line file down to the part they care about.
    """
    upload = st.session_state.get("upload")
    if upload is None:
        return
    try:
        name, text = pg.read_upload(upload)
    except ValueError as exc:
        st.session_state.error = str(exc)
        return
    st.session_state.source = text
    st.session_state.filename = Path(name).name
    st.session_state.outcome = None
    st.session_state.error = ""


def take_companion() -> None:
    """The same, for the already-converted file a spec imports."""
    upload = st.session_state.get("companion_upload")
    if upload is None:
        return
    try:
        name, text = pg.read_upload(upload)
    except ValueError as exc:
        st.session_state.error = str(exc)
        return
    st.session_state.companion_name = Path(name).name
    st.session_state.companion_source = text


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


def convert_suite(plan: pg.SuitePlan, only: list[str], *,
                  tree: dict[str, str] | None, area) -> None:
    """Run a whole suite and put the result in session state.

    The single-file `convert` above and this are the same four moves — make a
    thread, stream a run, read the final state — and differ in three ways:

    1. **It sends a config.** `max_concurrency` caps the fan-out and the
       `recursion_limit` has to grow with the wave count; without them a wide
       suite opens a connection per file and a deep one stops mid-run.
    2. **It ticks files off as they land.** The fan-out finishes `convert_file`
       once per file in completion order, and each of those updates carries the
       one outcome that branch appended — so eighty seconds of work can show its
       working instead of a spinner.
    3. **It sends either a tree or a pair of paths**, and `pg.suite_key` picks
       the credential to match: the owner's for a folder on this machine
       (`guard_run` refuses `root` for anybody else), the demo key for an
       upload (which must stay metered — the owner bypasses the meter).
    """
    st.session_state.suite_error = ""
    st.session_state.suite_outcome = None
    client = pg.suite_client(visitor=st.session_state.visitor)
    landed: list[pg.FileRow] = []

    try:
        thread_id = client.threads.create()["thread_id"]
        request = pg.suite_payload(st.session_state.suite_root,
                                   st.session_state.suite_out, only, tree=tree)
        context = pg.suite_context(
            model=st.session_state.suite_model,
            max_attempts=st.session_state.suite_attempts,
            user_id=pg.DEFAULT_USER if st.session_state.suite_recall else "",
        )
        config = pg.suite_config(waves=len(plan.waves),
                                 parallel=st.session_state.suite_parallel)
        final: dict = {}
        with area, st.status(f"Converting {plan.files} file(s)…", expanded=True) as status:
            st.write(plan.found)
            for update in pg.stream(client, thread_id, request, assistant="suite",
                                    config=config, context=context):
                if update.kind == "node":
                    for row in pg.rows_in(update.update):
                        landed.append(row)
                        st.write(f"{len(landed)}/{plan.files} · `{row.path}` — "
                                 f"{row.status} ({row.gates_line} gates, {row.seconds:.0f}s)")
                    if update.node == "next_wave":
                        wave = pg.wave_label(plan, (update.update or {}).get("wave", 0))
                        if wave:
                            st.write(wave)
                    elif update.node in {"plan", "finish"}:
                        st.write(pg.SUITE_NODE_LABELS.get(update.node, update.node))
                elif update.kind == "state":
                    final = update.state
            result = pg.suite_result(final)
            status.update(label=f"Finished — {result.headline}",
                          state="complete", expanded=False)
    except Exception as exc:  # noqa: BLE001 — every failure here is somebody else's server
        st.session_state.suite_error = pg.explain(exc)
        return

    st.session_state.suite_outcome = result


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

# --- the two things this page can convert -------------------------------------
# Streamlit renders BOTH tabs on every run and hides the one you are not looking
# at, so nothing below may do work on sight: everything expensive stays behind a
# button, and the folder scan in the suite tab is the one exception — cheap, no
# model, and the thing that makes the plan visible before you spend anything.

single_tab, suite_tab = st.tabs(["Single file", "Whole suite"])

with single_tab:
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
        # Upload, or paste. Both end in the same two session keys, because the
        # request is text either way — the file's bytes become `source_text`
        # and its name becomes the label. `on_change` rather than reading the
        # widget inline: a widget's value cannot be assigned after it has been
        # drawn, so filling the boxes has to happen before the re-run.
        st.file_uploader(
            "Upload a TypeScript Selenium file",
            type=["ts", "tsx", "js", "mjs", "cjs"],
            key="upload",
            on_change=take_upload,
            help="Or paste below. Nothing is stored: the file's text is sent "
                 "with the request and the result comes straight back.",
        )
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
            st.file_uploader("Upload the converted companion",
                             type=["ts", "tsx", "js", "mjs", "cjs"],
                             key="companion_upload", on_change=take_companion)
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

with suite_tab:
    st.subheader("Convert a whole Selenium folder")
    st.caption(
        "Page objects first, then the tests that import them — each wave in "
        "parallel, then the whole tree compiled as one project."
    )

    # Two ways in, and the difference between them is not convenience.
    #
    # **Upload** sends the folder as `source_tree`: relative path to text. The
    # graph materializes it into a temp directory it chooses, converts it, hands
    # the files back as text and deletes the workspace. Nothing in the request
    # names a path on the server, so it works against any backend — including a
    # public one, where it is metered per file.
    #
    # **A folder path** sends `root`/`out_root`, which name directories on the
    # machine the graph runs on. `guard.py` refuses those for anyone but the
    # owner, and should: on a public host `root: "/"` is a request to read the
    # machine. So it is offered only when the backend *is* this machine.
    folder_blocked = pg.folder_blocker()
    ways = ["Upload files"] + ([] if folder_blocked else ["A folder on this machine"])
    way = st.radio("How", ways, horizontal=True, label_visibility="collapsed",
                   key="suite_way") if len(ways) > 1 else ways[0]
    uploading = way == "Upload files"

    form_column, plan_column = st.columns([3, 2], gap="large")
    tree: dict[str, str] = {}

    with form_column:
        if uploading:
            uploads = st.file_uploader(
                "Drop your Selenium files, or a zip of the folder",
                type=["ts", "tsx", "js", "mjs", "cjs", "zip"],
                accept_multiple_files=True,
                key="suite_uploads",
                help="A zip keeps your folder structure, which is what the "
                     "import between a spec and its page object needs. Loose "
                     "files arrive without their directories — a browser does "
                     "not send those.",
            )
            tree, upload_complaint = pg.tree_from_uploads(uploads)
        else:
            upload_complaint = ""
            st.text_input("Suite folder", key="suite_root",
                          help="The Selenium folder to convert, on this machine.")
            st.text_input("Output folder", key="suite_out",
                          help="Created if it does not exist. Must not be inside the suite.")
            st.caption(
                "⚠️ This reads and writes folders on **this machine**, as you. "
                "Run Streamlit with `--server.address 127.0.0.1` if the network "
                "you are on is not one you trust."
            )

        with st.expander("Options"):
            st.text_input(
                "Only these files", key="suite_only",
                placeholder="pages/*.ts, LoginPage.ts",
                help="Comma-separated patterns matching the relative path or "
                     "the bare name. Blank means the whole folder.",
            )
            left_option, right_option = st.columns(2)
            left_option.slider("Files at a time", 1, 16, key="suite_parallel",
                               help="Caps the fan-out — how many files of one "
                                    "wave are converted at the same time.")
            right_option.slider("Attempts per file", 1, 3, key="suite_attempts",
                                help="1 = draft only. 3 = draft plus two repairs.")
            st.text_input("Model", key="suite_model",
                          placeholder="leave blank to use the server's S2P_MODEL",
                          help="A provider:model string, e.g. openai:gpt-5.4.")
            st.checkbox(
                "Use long-term memory", key="suite_recall",
                help="Applies remembered conventions to every file. Off sends "
                     "no user id, which reads and writes nothing.",
            )

    only = [p.strip() for p in st.session_state.suite_only.split(",") if p.strip()]
    if uploading:
        complaint = upload_complaint
    else:
        complaint = pg.check_suite(st.session_state.suite_root,
                                   st.session_state.suite_out, only)

    with plan_column:
        # `s2p scan`, drawn before the button. A suite run is the one thing on
        # this page that costs twelve conversions instead of one, so the number
        # of files and waves belongs on screen *before* it starts — and for an
        # upload the number that matters is what the meter will charge, which
        # counts every file sent, not only the convertible ones.
        plan = None
        if complaint:
            st.warning(complaint)
        elif uploading and not tree:
            st.info("Upload the files, or a zip of the folder, to see the plan.")
        else:
            try:
                plan = (pg.plan_tree(tree, only) if uploading
                        else pg.plan_suite(st.session_state.suite_root, only))
            except ValueError as exc:  # a tree the scanner would not accept
                st.warning(str(exc))
            if plan is not None and not plan.convert:
                st.warning("Nothing in there can be converted"
                           + (" with that filter." if only else "."))
                plan = None
        if plan is not None:
            st.markdown(f"**Plan** — {plan.line}")
            if uploading and not pg.is_local():
                st.caption(f"Costs **{plan.billable}** of today's conversions — "
                           "every file sent, including any carried across "
                           "untouched, because the meter cannot tell them apart "
                           "without doing the scan itself.")
                # Metering is per file, so a suite has a price before it has a
                # result — and a price the demo cannot pay should stop the
                # button rather than the request. A 429 after a click that
                # looked fine is the worst version of the same refusal.
                too_much = pg.affordable(limits, plan.billable)
                if too_much:
                    st.warning(too_much)
                    plan = None
            for number, wave in enumerate(plan.waves, start=1):
                with st.expander(f"Wave {number} — {len(wave)} file(s)",
                                 expanded=number == 1):
                    for path in wave:
                        st.markdown(f"`{path}`")
            if plan.copied:
                st.caption(f"Carried across untouched: {', '.join(plan.copied)}")
            if plan.skipped:
                with st.expander(f"Skipped — {len(plan.skipped)}"):
                    for path, why in plan.skipped:
                        st.markdown(f"`{path}` — {why}")

    started = st.button("Convert suite", type="primary", width="stretch",
                        disabled=plan is None, key="suite_go")

    progress = st.container()
    if started and plan is not None:
        convert_suite(plan, only, tree=tree if uploading else None, area=progress)
        st.rerun()

    if st.session_state.suite_error:
        st.error(st.session_state.suite_error)

    result = st.session_state.suite_outcome
    if result is None:
        if not st.session_state.suite_error:
            st.info(
                "The run appears here: one row per file, then what the whole "
                "tree adds up to — does it compile as one project, what public "
                "API changed shape, and every TODO(review) in one list."
            )
    else:
        if result.passed:
            st.success(f"**Passed.** {result.headline}")
        else:
            st.warning(f"**Needs review.** {result.headline}")

        # The converted suite, before anything else. An upload whose result you
        # cannot take away is a demo, not a tool.
        if result.tree:
            st.download_button(
                f"⬇ Download the converted suite — {len(result.tree)} file(s) + the report",
                pg.converted_zip(result.tree, result.markdown),
                file_name="playwright-suite.zip",
                mime="application/zip",
                type="primary",
                width="stretch",
            )
        elif result.report_path:
            st.caption(f"Written to `{Path(result.report_path).parent}`")

        counts = result.totals
        for column, (label, value) in zip(st.columns(4), [
            ("Passed", f"{counts['passed']}/{len(result.rows)}"),
            ("Needs review", str(counts["needs-review"])),
            ("Tree compiles", "YES" if result.compiles else "NO"),
            ("Elapsed", f"{result.elapsed:.0f}s"),
        ]):
            column.metric(label, value)

        files_tab, tree_tab, parity_tab, todo_tab, report_tab = st.tabs(
            ["Files", "Tree", "Parity", "TODOs", "Report"])

        with files_tab:
            st.dataframe(
                [{"file": row.path, "wave": row.wave, "status": row.status,
                  "gates": row.gates_line, "critic": row.critic or "—",
                  "attempts": row.attempts, "seconds": round(row.seconds, 1),
                  "why": row.reason}
                 for row in result.rows],
                width="stretch", hide_index=True,
            )
            for row in result.rows:
                if row.errors:
                    st.error(f"`{row.path}` — " + "; ".join(row.errors))
            if result.tree:
                chosen = st.selectbox("Read one", sorted(result.tree),
                                      key="suite_pick")
                st.code(result.tree[chosen], language="typescript", line_numbers=True)

        with tree_tab:
            # The reason step 9.3 exists: twelve green rows are twelve local
            # claims, and this is the one global one.
            st.caption(
                "Every per-file verdict above is a claim about that file "
                "compiling against the companions it happened to import. "
                "This is the converted folder compiled as **one project**."
            )
            if result.compiles:
                st.success(f"{result.tree_files} file(s) compile together.")
            elif result.tree_error:
                st.warning(f"The tree could not be compiled: {result.tree_error}")
            else:
                st.error(f"The tree does not compile — "
                         f"{len(result.tree_findings)} finding(s).")
                st.code("\n".join(result.tree_findings), language=None)

        with parity_tab:
            st.caption(
                "What the source exposed publicly, and what became of it. A "
                "rename is a guess; a removal with no reason is the line "
                "worth reading."
            )
            for column, (label, value) in zip(st.columns(4), [
                ("Kept", result.kept), ("Renamed", result.renamed),
                ("Removed", result.removed), ("Unexplained", result.unexplained),
            ]):
                column.metric(label, str(value))
            if result.losses:
                st.dataframe(
                    [{"file": path, "name": name, "verdict": verdict, "reason": reason}
                     for path, name, verdict, reason in result.losses],
                    width="stretch", hide_index=True,
                )
            else:
                st.markdown("Nothing lost or renamed.")

        with todo_tab:
            if result.todos:
                st.markdown("**Every TODO(review) in the suite, in one place**")
                for text, places in result.todos:
                    st.markdown(f"- {text}  \n  <small>{', '.join(places)}</small>",
                                unsafe_allow_html=True)
            else:
                st.markdown("**No TODO(review) items.**")
            if result.notes:
                st.markdown("**Notes**")
                for note in result.notes:
                    st.markdown(f"- {note}")

        with report_tab:
            if result.report_path:
                st.caption(f"Written to `{result.report_path}`")
            if result.markdown:
                st.download_button("Download conversion-report.md", result.markdown,
                                   file_name="conversion-report.md", mime="text/markdown")
                st.markdown(result.markdown)
            else:
                st.markdown("No report was written.")


st.divider()
st.caption(
    "Built with LangGraph. The agent, the four gates and this page are all in "
    "[github.com/varunbhatt2193/selenium2playwright]"
    "(https://github.com/varunbhatt2193/selenium2playwright)."
)
