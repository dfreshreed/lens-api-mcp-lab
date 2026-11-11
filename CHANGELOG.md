# Changelog

What changed in the lab, newest first. Commit types follow [Conventional Commits](https://www.conventionalcommits.org): `feat`, `fix`, `docs`, `chore`.

---

## 2026-11-11

### Docs

- **README:** rewrote the intro around the customer value and the "one question, three ways" flow (Script, You pick, AI picks)
- **README:** sample-data commands now say what each one returns, and point to `LAB_GUIDE.md` for adding real credentials
- **LAB_GUIDE:** change csv file name referenced through guide, now matches filename shown in sharepoint location

---

## 2026-11-10 · Lab Day

### Docs

- **Customer scenario** (`b0aee5c`): the lab guide now follows one customer, Sam, an IT admin, through every module
  - Each module opens with Sam's situation and what you'll get from it
  - Each step has a **Why:** line that ties it back to the scenario
  - Setup step 3 explains the two passes (Lens + AI), where to find each value in `lab-keys.csv`, a filled-in example and the common mistakes
  - New "Sam's Questions, Answered" table in Module 6
  - Key Terms moved to the end; checkpoint answers are hidden in click-to-open boxes
  - New big-picture image: _Three ways to ask Lens the same question_. Module images renamed (`same-steps.png`, `ai-model-picks-tool.png`)
- **Module 4** (`e827730`): the reads-schema step now asks a bounded question (offline peripherals, up to 10), followed by the same question with the Lens tools so you can compare the number of steps

### Fixes

- **Reads-schema server** (`e5d3bd0`): query results over 20,000 characters (up from 12,000) are cut with a note telling the AI model to ask for fewer fields, add a filter or use a smaller page size. Before, the cut was silent and the AI model kept retrying
- **AI client** (`8bc7f23`): default `LAB_MAX_STEPS` raised from 8 to 12, so schema-reading questions have room to finish
- **Config** (`82d8ae6`): `LAB_LLM_BASE_URL` in `.env.example` points at the new gateway Codespace

### Chores

- **Devcontainer** (`a0df83e`): terminal font size 14 » 12
- **Devcontainer** (`877cac2`): moved `workbench.panel.defaultLocation` in with the other `workbench` settings. No change in behaviour

---

## 2026-11-10 · Initial Release

- **Init** (`ddf7e9a`): Poly Lens + MCP lab
  - Lens API helper, offline-devices script, two MCP clients (you pick mcp tool/ AI picks mcp tool), two MCP servers (Lens tools / reads schema), setup check, sample data and the lab gateway
  - Lab guide with Modules 1–6
  - Codespaces devcontainer
