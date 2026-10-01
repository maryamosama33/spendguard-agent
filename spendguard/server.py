from mcp.server.mcpserver import MCPServer

from spendguard.extraction import extract_expense as _extract_expense

mcp = MCPServer("spendguard")


@mcp.tool()
def extract_expense(file_path: str, source_channel: str, sender: str) -> dict:
    """Extract structured expense fields from a photo, PDF, or scanned form.

    Args:
        file_path: path to the image/PDF to read.
        source_channel: "whatsapp" or "email".
        sender: the WhatsApp number or email address the request came from.
    """
    return _extract_expense(file_path, source_channel, sender).model_dump()


if __name__ == "__main__":
    mcp.run()
