# Poly Lens API + MCP Lab

This hands-on lab is a **demonstration** designed to help you experience the ways a customer could use the Lens API to optimize their workflow ans save time/effort. **This is a teaching lab, not a deployment guide. In production, use a central, governed MCP server**

You'll answer the question _"Which meeting rooms have a problem right now, and can I fix it without walking there?"_ three different ways:

- **A Script:** run a script that demonstrates how a developer would write it
- **You pick:** choose a ready-made MCP tool yourself
- **AI picks:** ask in plain English and let an AI model choose the tools

Everything runs in a GitHub Codespace in your browser: no installs, no admin rights, no AI account

- **Participants, start here: [LAB_GUIDE.md](LAB_GUIDE.md)**

You can quickly test with sample data by running the commands below. Copy each command, paste in the Codespace terminal, and press enter

```bash
LENS_MOCK=1 python script_offline_devices.py # returns 7 offline devices and a table showing device name, model, room, and when it was last seen
LENS_MOCK=1 python mcp_client_you_pick.py # returns 7 mcp server tools available
```
