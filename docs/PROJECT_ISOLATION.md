# Project Isolation

AutoMem supports project-scoped memory isolation, allowing a single Docker deployment to serve multiple projects with completely separate memory spaces.

## Overview

Project isolation ensures that memories from different projects are completely separated:
- **FalkorDB**: All Memory nodes include a `project_id` property
- **Qdrant**: Each project uses a separate collection (`{project_id}_memories`)
- **API**: All operations are scoped by the `X-Project-ID` header

## Configuration

### MCP Client Setup

Pass the project ID when connecting via MCP:

```bash
# Via header
curl https://mcp-server/mcp/sse \
  -H "Authorization: Bearer your-token" \
  -H "X-Project-ID: my-project"

# Via query parameter
curl "https://mcp-server/mcp/sse?project_id=my-project" \
  -H "Authorization: Bearer your-token"

# Via environment variable (server-side default)
export AUTOMEM_PROJECT_ID=my-project
```

### Direct API Usage

Include the project ID in all API requests:

```bash
curl -X POST http://localhost:8001/memory \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "X-Project-ID: my-project" \
  -H "Content-Type: application/json" \
  -d '{"content": "Project-specific memory"}'
```

## Project ID Format

Project IDs must contain only alphanumeric characters, hyphens, and underscores:
- ✅ Valid: `my-project`, `project_1`, `web-app-dev`
- ❌ Invalid: `my project`, `project.1`, `special@chars`, `__reserved`

**Note**: Project IDs starting with `__` (double underscore) are reserved for system use.

## Backward Compatibility

- Existing clients without `X-Project-ID` use the default project (`__default__`)
- The default project uses the original collection name (`memories`)
- Existing memories without project_id property are automatically migrated to `__default__` at startup
- Automatic migration runs on first startup after upgrade (idempotent)
- No breaking changes for existing deployments

## Multi-Project Usage

### Example: Development vs Production

```bash
# Development environment
export AUTOMEM_PROJECT_ID=myapp-dev
# Creates: myapp-dev_memories collection

# Production environment
export AUTOMEM_PROJECT_ID=myapp-prod
# Creates: myapp-prod_memories collection
```

### Example: Multiple Applications

```javascript
// App 1: E-commerce
const client1 = new AutoMemClient({
  endpoint: "https://automem.example.com",
  projectId: "ecommerce-app"
});

// App 2: Analytics Dashboard
const client2 = new AutoMemClient({
  endpoint: "https://automem.example.com",
  projectId: "analytics-dashboard"
});
```

## Collection Management

### Automatic Creation

Qdrant collections are created automatically when first accessed:
- Format: `{project_id}_memories`
- Default project (`__default__`) uses: `memories` (backward compatibility)
- Includes proper indexing for tags and importance

### Manual Collection Management

```bash
# List collections for all projects
curl -H "Authorization: Bearer $QDRANT_API_KEY" \
  "$QDRANT_URL/collections"

# Delete project collection (careful!)
curl -X DELETE -H "Authorization: Bearer $QDRANT_API_KEY" \
  "$QDRANT_URL/collections/myproject_memories"
```

## Monitoring

### Health Check

The `/health` endpoint shows database connectivity but is not project-scoped:

```bash
curl http://localhost:8001/health
# Returns overall service health
```

### Project-Specific Stats

Use `/analyze` endpoint with project header:

```bash
curl -H "X-Project-ID: my-project" \
  -H "Authorization: Bearer $TOKEN" \
  http://localhost:8001/analyze
```

## Security Considerations

- Project isolation is enforced at the API layer
- No cross-project data access possible
- Each project effectively has its own memory space
- Use different API tokens per project for additional security

## Troubleshooting

### Common Issues

**"Memory not found" errors:**
- Ensure requests include correct `X-Project-ID` header if using named projects
- Omit `X-Project-ID` header to access default project (backward compatibility)
- Check that project ID format is valid
- Project IDs starting with `__` are reserved for system use

**Vector search not working:**
- Confirm Qdrant collection exists for the project
- Check collection naming: `{project_id}_memories`
- Verify vector dimensions match (default: 768)

**Performance with many projects:**
- Each project creates separate Qdrant collections
- Consider cleanup of unused project collections
- Monitor memory usage with large numbers of projects

### Debugging

Enable debug logging to trace project isolation:

```bash
# Set log level to DEBUG
export LOG_LEVEL=DEBUG

# Check API logs for project ID extraction
tail -f automem.log | grep "project"
```

## Best Practices

1. **Consistent Naming**: Use clear, consistent project ID conventions
2. **Environment Separation**: Different project IDs for dev/staging/prod
3. **Cleanup**: Remove unused project collections periodically
4. **Monitoring**: Track collection sizes and query patterns per project
5. **Documentation**: Document project IDs used in your organization

## Limitations

- Project isolation is API-level only (database admin can see all data)
- No built-in project management UI
- Collection names limited by Qdrant naming rules
- No automatic cleanup of abandoned project collections