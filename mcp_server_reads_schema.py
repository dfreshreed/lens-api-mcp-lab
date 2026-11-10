"""
mcp_server_reads_schema.py » the MCP server that only reads the schema

Knows nothing about Poly Lens except how to connect. Three generic tools that work for any GraphQL API:
    schema_overview   what can this API do? (read from the API's own schema)
    describe_type     what fields does this type have?
    run_query         run a read-only query the AI wrote itself

No device tools, no room logic, no hand-written queries. Compare with mcp_server_lens_tools.py.
This works because GraphQL describes itself (introspection): an AI can read the API's manual
and write its own queries. A REST API without a published spec needs a tool built for each feature.

    LAB_SERVER=mcp_server_reads_schema.py python mcp_client_ai_picks.py "Which devices are offline?"

Works best with real Lens data (LENS_MOCK=0); sample data only covers the lab's own queries.
"""

import json

from mcp.server.fastmcp import FastMCP

from lens_api_helper import execute_gql  # only the connection: URL + login


# -- config constants

MAX_RESULT_CHARACTERS = 20000  # keep large answers from flooding the AI

SCHEMA_OVERVIEW = """
query SchemaOverview {
    __schema {
        __KIND__ {
            fields {
                name
                description
                args { name type { name kind ofType { name } } }
            }
        }
    }
}
"""

DESCRIBE_TYPE = """
query DescribeType($typeName: String!) {
    __type(name: $typeName) {
        name
        kind
        description
        fields { name description type { name kind ofType { name kind } } }
        inputFields { name description type { name kind ofType { name kind } } }
        enumValues { name description }
    }
}
"""

mcp = FastMCP("reads-schema", log_level="WARNING")


# -- TOOLS


@mcp.tool()
def schema_overview(kind: str = "query") -> str:
    """List every operation this GraphQL API offers, from its own schema.
    kind: 'query' (read) or 'mutation' (change). Start here to see what is possible."""
    schema_key = "queryType" if kind == "query" else "mutationType"
    data = execute_gql(SCHEMA_OVERVIEW.replace("__KIND__", schema_key))
    schema = (data.get("data") or {}).get("__schema") or {}
    fields = (schema.get(schema_key) or {}).get("fields") or []

    operations = []
    for field in fields:
        # each argument's type name (wrapped types like String! keep the name one level down)
        args = {}
        for arg in field.get("args") or []:
            arg_type = arg.get("type") or {}
            args[arg["name"]] = arg_type.get("name") or (arg_type.get("ofType") or {}).get("name")

        operations.append({
            "name": field["name"],
            "description": (field.get("description") or "")[:160],
            "args": args,
        })
    return json.dumps(operations, indent=1)


@mcp.tool()
def describe_type(type_name: str) -> str:
    """Show the fields (or input fields) of one type, with descriptions, so you can write a correct query.
    Use it on the types named in schema_overview, and on the types of their arguments."""
    return json.dumps(execute_gql(DESCRIBE_TYPE, {"typeName": type_name}), indent=1)


@mcp.tool()
def run_query(query: str, variables: dict | None = None) -> str:
    """Run a read-only GraphQL query you wrote. Mutations are rejected.
    If the API answers with errors, read them: they say which field or argument is wrong."""
    if query.lstrip().lower().startswith("mutation"):
        return "Rejected: this server is read-only."
    result_text = json.dumps(execute_gql(query, variables), indent=1)
    if len(result_text) <= MAX_RESULT_CHARACTERS:
        return result_text

    # tell the AI model the result was cut, so it doesn't treat a partial list as complete
    return (
        result_text[:MAX_RESULT_CHARACTERS]
        + f"\n[result cut at {MAX_RESULT_CHARACTERS:,} of {len(result_text):,} characters: "
        "ask for fewer fields, add a filter, or use a smaller pageSize]"
    )


if __name__ == "__main__":
    mcp.run()
