# Native document uploads

The application stages a native browser upload in Cloud Storage only while the
document worker needs the source material. A worker must delete the object
before it marks the intake event as complete. This is deliberately not a
long-term document store.

## Provisioning

Create one private bucket per environment in the same region as the Cloud Run
services. For production the configured name is
`healthcare-credentialing-prod-native-uploads`.

```sh
gcloud storage buckets create gs://healthcare-credentialing-prod-native-uploads \
  --project=healthcare-credentialing-prod \
  --location=us-east1 \
  --uniform-bucket-level-access
```

Apply a one-day lifecycle rule. This is a backstop for a browser upload that is
abandoned before it can be queued; successful and terminal documents are
deleted immediately by the worker.

```json
{
  "rule": [
    {
      "action": { "type": "Delete" },
      "condition": { "age": 1 }
    }
  ]
}
```

```sh
gcloud storage buckets update gs://healthcare-credentialing-prod-native-uploads \
  --lifecycle-file=ops/native-upload-lifecycle.json
```

Use this CORS policy so the dashboard can PUT to its own short-lived signed
URLs. Do not add wildcard origins.

```json
[
  {
    "origin": ["https://app.cozemahealth.com"],
    "method": ["PUT"],
    "responseHeader": ["Content-Type"],
    "maxAgeSeconds": 3600
  }
]
```

```sh
gcloud storage buckets update gs://healthcare-credentialing-prod-native-uploads \
  --cors-file=ops/native-upload-cors.json
```

Grant `credentialing-drive-runtime@healthcare-credentialing-prod.iam.gserviceaccount.com`
object read/write/delete access only on this bucket. It also needs permission to
sign a short-lived upload URL using IAM Credentials (`roles/iam.serviceAccountTokenCreator`
on that service account). No browser identity receives bucket access.

```sh
gcloud storage buckets add-iam-policy-binding gs://healthcare-credentialing-prod-native-uploads \
  --member=serviceAccount:credentialing-drive-runtime@healthcare-credentialing-prod.iam.gserviceaccount.com \
  --role=roles/storage.objectUser
gcloud iam service-accounts add-iam-policy-binding \
  credentialing-drive-runtime@healthcare-credentialing-prod.iam.gserviceaccount.com \
  --member=serviceAccount:credentialing-drive-runtime@healthcare-credentialing-prod.iam.gserviceaccount.com \
  --role=roles/iam.serviceAccountTokenCreator \
  --project=healthcare-credentialing-prod
```

Configure `NATIVE_UPLOAD_BUCKET` for both the dashboard and document worker.
The Better Auth production Cloud Build configuration already supplies that
value; provision the bucket and IAM/CORS policy before deploying this feature.
