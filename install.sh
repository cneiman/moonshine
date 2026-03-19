#!/bin/bash
# moonshine installer
# Creates the database, installs dependencies, and prints next steps.

set -e

echo "🧠 Setting up moonshine..."

# Create memories.db
if [ ! -f memories.db ]; then
  echo "Creating memories.db..."
  sqlite3 memories.db < core/schema.sql
  echo "✅ Database created"
else
  echo "⏭️  memories.db already exists"
fi

# Install Python dependencies
echo "Installing Python dependencies..."
if command -v pip3 &>/dev/null; then
  pip3 install --user requests 2>/dev/null || echo "⚠️  Could not install 'requests' — install manually: pip3 install requests"
elif command -v pip &>/dev/null; then
  pip install --user requests 2>/dev/null || echo "⚠️  Could not install 'requests' — install manually: pip install requests"
else
  echo "⚠️  pip not found. Install 'requests' manually: pip3 install requests"
fi

# Make mem CLI executable
chmod +x core/mem

# Install observer dependencies
if [ -d observer ]; then
  echo "Installing observer dependencies..."
  cd observer && npm install && cd ..
  echo "✅ Observer ready"
fi

# Create starter files if they don't exist
for f in MEMORY.md SOUL.md USER.md; do
  if [ ! -f "$f" ]; then
    cp "templates/$f" "$f"
    echo "📝 Created $f from template"
  fi
done

echo ""
echo "✅ moonshine is ready!"
echo ""
echo "Next steps:"
echo "  1. Edit MEMORY.md, SOUL.md, and USER.md with your info"
echo "  2. Connect to your AI tool:"
echo "     Claude Code: cp adapters/claude-code/.mcp.json .mcp.json"
echo "     OpenClaw:    cp -r adapters/openclaw/ ~/your-workspace/hooks/conversation-observer/"
echo "     Cursor:      See adapters/cursor/README.md"
echo "  3. Start a conversation — the MCP server provides memory tools automatically"
echo ""
echo "📖 Full docs: https://github.com/cneiman/moonshine"
