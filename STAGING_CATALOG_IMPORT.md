# Staging catalog import

The staging catalog fixture contains only public application catalog data:

- universities
- vendors and their Cloudinary image references
- cafeteria categories
- menu items and vendor/menu relationships

It intentionally excludes users, administrator credentials, customer profiles,
addresses, carts, orders, favorites, ratings, tokens, and all other private data.

## One-time staging import

In the Render staging web service, add these environment variables:

```text
DEPLOYMENT_ENVIRONMENT=staging
DJANGO_CATALOG_IMPORT_ENABLED=True
```

Deploy the staging branch. The build runs migrations and then imports the
fixture. It imports only when all four catalog tables are empty. If any catalog
data already exists, it skips the entire import rather than overwriting data.

Confirm the Render build log contains `Staging catalog imported successfully`,
then set `DJANGO_CATALOG_IMPORT_ENABLED=False`.

The command refuses to run unless `DEPLOYMENT_ENVIRONMENT` is exactly
`staging`, which prevents an accidental production import.
