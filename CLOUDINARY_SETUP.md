# Cloudinary media storage

Swift Delivery keeps local uploads in `media/` by default and can use
Cloudinary for staging and production. PostgreSQL stores only each image's
name; Cloudinary stores and serves the image bytes.

## Environment variables

Keep real credentials in `.env` locally and in the Render dashboard for
deployed services. Never place the API secret in frontend variables or commit
it to Git.

```env
CLOUDINARY_URL=cloudinary://api_key:api_secret@cloud_name
USE_CLOUDINARY=False
CLOUDINARY_FOLDER=swift-delivery/development
```

With `USE_CLOUDINARY=False`, Django continues to use `MEDIA_ROOT`. To test
Cloudinary locally, change it to `True` and restart the Django server.

Use distinct folders for deployed environments:

```env
# Staging
USE_CLOUDINARY=True
CLOUDINARY_FOLDER=swift-delivery/staging

# Production
USE_CLOUDINARY=True
CLOUDINARY_FOLDER=swift-delivery/production
```

Each Render web service also needs its own `CLOUDINARY_URL` secret. Do not put
the real URL in `render.yaml`.

## Migrating existing media

The migration command considers only files referenced by the `Vendor.image`,
`Vendor.logo`, and `MenuItem.image` database fields. It does not upload cache
files or unrelated contents of `media/`.

Preview first:

```powershell
python manage.py migrate_media_to_cloudinary --folder swift-delivery/staging
```

After reviewing the preview, upload the referenced files. Their database names
remain unchanged so local filesystem mode continues to work:

```powershell
python manage.py migrate_media_to_cloudinary --folder swift-delivery/staging --execute
```

Run the command against the database for the environment being migrated. Back
up a production database before running it against production. The command is
resumable: assets already present in the selected Cloudinary folder are reused.

## Deployment order

1. Preview and migrate existing images.
2. Confirm the migrated Cloudinary URLs load.
3. Add the three environment variables to the corresponding Render service.
4. Deploy the storage code.
5. Test creating and replacing a vendor logo and menu image.
6. Only then commit the removal of Git-tracked files under `media/`.
