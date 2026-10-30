# **Code Futures Report: `oss-slu/tidewater-atlas`**

> ### **Fabricated example**
>
> `oss-slu/tidewater-atlas` is not a real repository. No project by that name exists in `oss-slu` or anywhere else, and nothing here describes any actual team's codebase. It is composite: it carries a mix of findings chosen so that every kind of outcome appears somewhere, which no single real project would be likely to show at once.
>
> Your report will have this shape. Only the findings differ.

|                    |                                                        |
| ------------------ | ------------------------------------------------------ |
| **Repository**     | `oss-slu/tidewater-atlas`                              |
| **Commit**         | `a0408fc`                                              |
| **Collected**      | 2026-11-01 14:00 UTC                                   |
| **Schema**         | `code-futures/v1`                                      |
| **Languages**      | Python, JavaScript                                     |
| **Classification** | Internal — shareable within the program, not published |

---

## **How to read this**

This report describes which engineering practices are in place in one repository. It records practice. Judging the code is a separate job, done by people, and nothing here feeds an evaluation.

Each finding carries one of four outcomes:

* **enforced** — in place, and CI runs it
* **present** — in the repository, but nothing runs it automatically
* **absent** — we looked, and it is not there
* **unreadable** — we could not find out. This describes our tooling, not your repository

The companion guidebook explains each probe and the limits of what it shows. Read §3 before acting on anything below.

---

## **Outcomes**

| Outcome    | Count |
| ---------- | ----- |
| enforced   | 9     |
| present    | 18    |
| absent     | 8     |
| unreadable | 1     |

### **Measurements, not a score**

This report carries numbers, and reading them well is most of what the guidebook is for. A measurement is data: 62% of lines executed, four files changing together, 340 points of cognitive complexity. A score is data collapsed into a judgement about whether you are doing well. The numbers here are the first kind, and nothing in this report performs the collapse.

Two numbers are deliberately absent. We do not publish the OpenSSF Scorecard's 0–10 aggregate, because it is calibrated for widely-depended-upon libraries and measures a population your project is not in — a validity problem rather than an objection to numbers. And we do not carry SonarQube's A–E maintainability letter, because a letter *is* a collapse into judgement; the underlying debt measurement it derives from appears instead.

Use the outcome counts as an index to find your place. Each probe reports on one practice, and the practices a project needs depend on what it is for.

36 findings from 34 probes, because `lint` and `format` are answered once per language.

---

## **1. Structure**

What the shape of this codebase looks like, measured from its own history. This is the only section about the code rather than the process around it.

* **`structure.hotspots`** — **present**. 4 files where change frequency and complexity both concentrate. Top: `api/services/sync.py`, changed 67 times, complexity 412.
    * `api/services/sync.py`
    * `api/models/survey.py`
    * `web/src/Dashboard.jsx`
    * `api/routes/upload.py`
* **`structure.change_coupling`** — **present**. 3 file pairs change together in more than 60% of the commits touching either. Strongest: `api/models/survey.py` and `api/routes/upload.py`, 83% across 24 commits.
    * api/models/survey.py <-> api/routes/upload.py (83%)
    * api/services/sync.py <-> web/src/Dashboard.jsx (71%)
    * api/models/survey.py <-> web/src/SurveyForm.jsx (64%)
* **`structure.untested_modules`** — **present**. 3 frequently-changed source files have no corresponding test file.
    * `api/services/sync.py`
    * `api/routes/upload.py`
    * `web/src/Dashboard.jsx`
* **`structure.duplication`** — **present**. 7.4% of lines are duplicated, across 18 blocks.
* **`structure.complexity`** — **present**. 1840 points of cognitive complexity across 63 files. Highest: `api/services/sync.py` at 412.
* **`structure.cohesion`** — **present**. 2 of 3 strongly-coupled pairs cross a module boundary (67%).
    * api/services/sync.py <-> web/src/Dashboard.jsx (71%)
    * api/models/survey.py <-> web/src/SurveyForm.jsx (64%)
* **`structure.blast_radius`** — **present**. A typical change touches 2 files. The largest tenth touch 19 or more; the largest touched 41.
    * `api/models/survey.py (in 14 large commits)`
    * `api/services/sync.py (in 11 large commits)`
    * `web/src/Dashboard.jsx (in 9 large commits)`
* **`structure.tidy_first`** — **present**. `api/services/sync.py` would pay back soonest: changed 67 times, complexity 412, no test file, 1 coupling across a module boundary.
    * `api/services/sync.py — changed 67 times, complexity 412, no test file, 1 coupling across a module boundary`
    * `api/models/survey.py — changed 52 times, complexity 180, 1 coupling across a module boundary`
    * `web/src/Dashboard.jsx — changed 44 times, complexity 210, no test file`

### **Reading a hotspot**

A hotspot is complexity multiplied by change frequency. Complicated code nobody touches is cheap, and simple code that changes constantly is cheap. The intersection is expensive, and it is where your next defect is most likely to appear.

Complexity here is approximated by indentation depth rather than parsed per language, which is an approximation and works the same way across every language in the portfolio. Treat the ranking as a direction to look, not a measurement of quality.

A hotspot with no tests is the combination worth acting on first: the code most likely to break, with the least protection while you change it.

### **Reading change coupling**

Two files that keep changing together are coupled, whether or not either imports the other. This is Kent Beck's definition made measurable: coupling is the probability that changing one thing forces you to change another, and your history records how often that actually happened.

A static analyser cannot see this. A test file changing with the module it tests is coupling working as intended. A route handler that changes every time a database model changes is a boundary in the wrong place, and it will keep costing you until it moves.

The question to ask of each pair is whether the relationship is one you would choose. Where the answer is no, that is where a refactor pays for itself.

### **Coupling that crosses a boundary**

Cohesion is coupling put in the right place. Things that change together should be *together*, so the same measurement reads two ways depending on where the pair sits.

A pair inside one module is cohesion working, and needs nothing from you. A pair spanning two modules is a boundary that is not holding, and it is the one worth your attention: every change has to cross it, and crossing costs a context switch, a second review, and a chance to miss one side.

Three questions, in order. Does the dependency run the direction you intended? Should one of these files move? Or is there a third thing — a shared concept neither module owns yet — that both are reaching for?

### **What a change costs you**

Kent Beck's argument about cost runs in a chain: the cost of software is the cost of changing it, the cost of changing it is dominated by the *expensive* changes rather than the average one, and coupling is what makes a change expensive.

So the gap between your median change and your largest tenth is the measurement that matters, not the median on its own. A codebase where most changes touch two files and the worst touch twenty has somewhere that twenty-file changes come from, and the files listed above are where to look.

This number falls when coupling falls. It is the clearest way to see whether structural work paid off.

### **Tidy first?**

The question mark is the point, and it is Beck's. Tidying pays off when it makes a change you are about to make easier. Tidying you will not cash in soon is cost without return, and a report listing every structural weakness invites exactly the reading he argues against.

So this is not the worst file in the repository. It is the file where tidying would pay back soonest: you are already in it regularly, it is expensive to work in, and the reasons above say why.

The test for whether you got this right is simple. If the next change to that area is easier than the last one was, it paid. If you tidied something you then did not touch for a month, it did not, and that is worth noticing rather than repeating.

### **What to look at by hand**

Everything above is what a machine can establish from your history and your files. Several of the most valuable structural questions are not on that list, and they are not on it because no tool answers them, rather than because they do not matter.

Worth an hour with the codebase and a colleague:

* **Is the domain model right?** The most expensive mistakes in software are conceptual — the wrong abstraction, a boundary that does not match the problem. Nothing above can see it.
* **Does the dependency direction match the design?** Coupling shows which files change together, not which *should* depend on which.
* **Is there duplicated *concept* rather than duplicated text?** Two implementations of one idea, written differently, are invisible to a duplication detector and are the more expensive kind.
* **Do the tests assert anything?** A test that runs code and checks nothing passes and counts toward coverage.
* **Is a hotspot complex for a good reason?** A parser or a scheduler may be the hardest file you have, correctly.
* **Is dead code still in the tree?** Detectable per language, and we do not yet run those tools.

The *Codebase Technical Assessment Guidebook and Rubric* is the instrument for this pass, across seventeen dimensions. Section 8 says how the two fit together.

**A caveat that applies to everything above.** Coupling is contextual. Two files coupled with respect to one kind of change may be independent with respect to another, and the figures here aggregate across every change in your history. They cannot tell you which kind of change is costing you. You can.

---

## **2. Open Source Baseline**

Whether the repository carries what an outside contributor needs. Someone who has never met you should be able to understand the project and open a pull request without asking a person first.

* **`practice.readme`** — **present**. `README.md`
* **`practice.readme_sections`** — **present**. Covers two of four expected sections. Missing: Installation, Project Structure.
* **`practice.contributing`** — **present**. `CONTRIBUTING.md`
* **`practice.code_of_conduct`** — **absent**. Searched the root, `docs/`, and `.github/`.
* **`practice.license`** — **present**. `LICENSE` exists. Its text matches no OSI-approved licence we recognise, so no SPDX identifier was assigned.
* **`practice.issue_template`** — **present**. `.github/ISSUE_TEMPLATE/bug_report.md`
* **`practice.pull_request_template`** — **absent**. No pull request template found.
* **`practice.security_policy`** — **absent**. No `SECURITY.md`.

### **Unidentifiable licence**

An unrecognisable licence file is a different finding from a missing one. Someone decided this project should be licensed. What is missing is the part that makes the decision legible to a machine, a contributor, or a lawyer. A retyped or truncated licence is the usual cause. Replacing it with verbatim text from [choosealicense.com](https://choosealicense.com) takes ten minutes.

---

## **3. Linting and Formatting**

Whether a tool checks style and common errors, and whether anything makes that check run.

| Probe    | Language   | Outcome      | Evidence                                                       |
| -------- | ---------- | ------------ | -------------------------------------------------------------- |
| `lint`   | JavaScript | **present**  | `eslint` in `frontend/.eslintrc.json`. No CI step runs it.     |
| `lint`   | Python     | **enforced** | `ruff` in `pyproject.toml`, run at `.github/workflows/ci.yml`. |
| `format` | JavaScript | **absent**   | No formatter configured.                                       |
| `format` | Python     | **enforced** | `ruff format` checked at `.github/workflows/ci.yml`.           |

### **two languages, two answers**

This repository is JavaScript and Python, so each language gets its own row. They disagree. Closing the gap costs a few lines of YAML, which makes it the cheapest real improvement available here. The guidebook calls the pattern **"Configured But Never Run"**, and §5 works through it.

---

## **4. Testing**

Which kinds of testing this project runs.

| Probe              | Outcome      | Evidence                                                         |
| ------------------ | ------------ | ---------------------------------------------------------------- |
| `test.unit`        | **enforced** | `pytest` in `pyproject.toml`, run at `.github/workflows/ci.yml`. |
| `test.integration` | **absent**   | No separate job, no marked suite.                                |
| `test.e2e`         | **absent**   | No Playwright, Cypress, or Selenium.                             |
| `test.coverage`    | **present**  | `pytest-cov` declared. No CI step runs it.                       |
| `test.mutation`    | **absent**   | No mutation testing tool.                                        |

### **Kinds, not percentages**

Coverage counts lines executed. Tests that assert nothing still count toward it. Naming which kinds run shows you which kind you lack, and that is something you can act on.

### **Absence is often correct**

Three of five kinds are absent here, which may well be right for a project at this stage. End-to-end tests on a service with no user-facing flows would be scaffolding for something due to be rewritten. This section reports which kinds run, not which kinds should.

---

## **5. Review and Integration**

Whether code is reviewed before merging, and whether anything runs against it.

* **`review.required`** — **present**. `main` is protected. Required approving reviews: 0. Status checks: not required. Administrators: exempt.
* **`review.practiced`** — **enforced**. 18 of the last 20 merged pull requests carried an approving review.
* **`ci.present`** — **enforced**. 3 workflows: `CI`, `CodeQL`, `Dependabot Updates`.

### **Policy and practice disagree**

The rule on the default branch and what actually happens are not the same thing here, and the two probes above measure different things on purpose.

Required review proves a review was *requested*. It cannot tell whether anyone read the diff: an approval nine seconds after a pull request opens counts identically to a careful read. Equally, a repository that requires nothing can still review every change well.

Only your team knows which of these you are. The numbers point at the question.

### **Protection that permits unreviewed merges**

Branch protection here requires zero approving reviews. The settings page shows a protected branch, and any pull request can still merge unread. §7 of the guidebook covers what making it binding costs, and there are real costs.

### **Administrators are exempt**

Whoever holds admin on this repository bypasses the rules above. On a small team that is most of the team, so the rules describe an intention. Sometimes that is the right call, because someone has to be able to fix a broken `main` at short notice. Worth choosing rather than inheriting.

---

## **6. Security and Dependencies**

Whether the project guards against known classes of problem, and keeps its dependencies current.

* **`security.sast`** — **unreadable**. The Scorecard SAST check exceeded its timeout. We established nothing here.
* **`security.token_permissions`** — **absent**. Workflows declare no top-level `permissions:` block, so jobs run with the repository default.
* **`security.workflow_safety`** — **enforced**. No dangerous workflow patterns detected.
* **`deps.update_tool`** — **enforced**. Dependabot at `.github/dependabot.yml`.
* **`deps.known_vulnerabilities`** — **present**. 3 dependencies carry known advisories.

### **An unreadable probe describes us**

`security.sast` failed on our side, and we report it that way. Marking it **absent** would tell you something was missing that may well be there.

If an outcome in your report reads **unreadable** and you believe it is wrong, tell us. That is a bug in our tooling rather than a dispute about your codebase.

---

## **7. Code Quality Analysis**

What continuous static analysis reports. This section carries no grade: it records whether the analysis runs and whether anyone has acted on what it flags.

* **`quality.project`** — **enforced**. Analysed as `oss-slu_tidewater-atlas`. Last analysis 2026-10-30.
* **`quality.gate`** — **enforced**. Quality gate `default`, applied in CI. Current status: Passed.
* **`quality.hotspots_reviewed`** — **present**. 7 security hotspots flagged, 2 reviewed. The rest are waiting on a person.

### **A hotspot is a question, not a defect**

A security hotspot is security-sensitive code that a person needs to look at. Sonar is not claiming a vulnerability here. It has found the place where the question lives and handed the question to you, which is what a good tool does when it cannot be certain.

Reviewing one means deciding whether the context makes it safe, then recording that decision. Most reviews end in "this is fine", and that is a result rather than a waste: the next person can see it was considered.

### **This analysis is public**

SonarQube Cloud publishes analysis of public repositories on sonarcloud.io, where anyone can see it without an account. The code is already public, so this adds no disclosure. We are telling you because finding out afterwards is a different experience from being told.

---

## **What we could not read**

| Probe           | Reason                                        |
| --------------- | --------------------------------------------- |
| `security.sast` | The Scorecard SAST check exceeded its timeout |

1 of 34 probes. A long list here, or a complete one, would mean the sweep failed rather than the repository being empty.

---

## **Where to start**

Pick one item. A team that picks six finishes none, because each competes with feature work that has a client attached. A team that picks one finishes it and picks another. §5 of the guidebook makes the argument properly.

Our suggestion here is **the dependencies with known advisories**:

* Each one is a published vulnerability in code you ship
* Usually a version bump rather than a change you have to design

Then, in order:

1. **Add an identifiable licence.** Copy verbatim text from [choosealicense.com](https://choosealicense.com).
2. **Fill in the missing README sections.** Overview, Tech Stack, Installation, and Project Structure are what a reader arriving cold needs.
3. **Run `eslint` in CI.** The configuration exists; this adds a step that invokes it. Expect many findings on first run — the guidebook covers landing a linter without a hundred-file diff.

Every item here is a future rather than a feature. None will be visible to a user, which is why they are easy to postpone, and why naming them is worth doing.

---

*Generated by [Code Futures](https://github.com/oss-slu/code-futures), schema `code-futures/v1`. Companion guidebook: [`guidebook_code_futures`](https://github.com/oss-slu/guidebook_code_futures). This report describes a repository rather than the people working in it, and feeds no evaluation.*
