# Poly Lens API + MCP Lab

This hands-on lab is designed to demonstrate the customer value in using the Lens API to optimize their workflow and solve a problem. You'll answer one customer question three different ways:

- **A Script:** run a script that demonstrates how a developer would write it
- **You pick:** choose a ready-made MCP tool yourself
- **AI picks:** ask in plain English and let an AI model choose the tools

Everything runs in a GitHub Codespace in your browser: no installs, no admin rights, no AI account

- **Participants, start here: [LAB_GUIDE.md](LAB_GUIDE.md)**

To use real data from Poly Lens and the LLM, you'll add credentials in the **`.env`** file (process covered in [LAB_GUIDE.md](LAB_GUIDE.md))

**HOWEVER**, you can quickly test with sample data by running the commands below. Copy each command, paste in the Codespace terminal, and press enter

```bash
LENS_MOCK=1 python script_offline_devices.py # returns 7 offline devices and a table showing device name, model, room, and when it was last seen
LENS_MOCK=1 python mcp_client_you_pick.py # returns 7 mcp server tools available
```
