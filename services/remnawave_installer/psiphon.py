import base64


PROFILE_SOURCE = (
    "https://raw.githubusercontent.com/IRNova/Nova-Server/904b35589e16ac78588d2cea8c20d02dd0e926ae/psiphon.config"
)
PROFILE_SHA256 = "b57d29cbe288fbea1f7bb59b914d331057d4880746e89df2eb928a3e678161b4"
SERVER_LIST_URL = "https://s3.amazonaws.com//psiphon/web/mjr4-p23r-puwl/server_list_compressed"
SERVER_LIST_PUBLIC_KEY = "MIICIDANBgkqhkiG9w0BAQEFAAOCAg0AMIICCAKCAgEAt7Ls+/39r+T6zNW7GiVpJfzq/xvL9SBH5rIFnk0RXYEYavax3WS6HOD35eTAqn8AniOwiH+DOkvgSKF2caqk/y1dfq47Pdymtwzp9ikpB1C5OfAysXzBiwVJlCdajBKvBZDerV1cMvRzCKvKwRmvDmHgphQQ7WfXIGbRbmmk6opMBh3roE42KcotLFtqp0RRwLtcBRNtCdsrVsjiI1Lqz/lH+T61sGjSjQ3CHMuZYSQJZo/KrvzgQXpkaCTdbObxHqb6/+i1qaVOfEsvjoiyzTxJADvSytVtcTjijhPEV6XskJVHE1Zgl+7rATr/pDQkw6DPCNBS1+Y6fy7GstZALQXwEDN/qhQI9kWkHijT8ns+i1vGg00Mk/6J75arLhqcodWsdeG/M/moWgqQAnlZAGVtJI1OgeF5fsPpXu4kctOfuZlGjVZXQNW34aOzm8r8S0eVZitPlbhcPiR4gT/aSMz/wd8lZlzZYsje/Jr8u/YtlwjjreZrGRmG8KMOzukV3lLmMppXFMvl4bxv6YFEmIuTsOhbLTwFgh7KYNjodLj/LsqRVfwz31PgWQFTEPICV7GCvgVlPRxnofqKSjgTWI4mxDhBpVcATvaoBl1L/6WLbFvBsoAUBItWwctO2xalKxF5szhGm8lccoc5MZr8kfE0uxMgsxz4er68iCID+rsCAQM="


def public_psiphon_config() -> dict:
    """Возвращает публичный профиль IRNova из закреплённого источника."""
    return {
        "ClientPlatform": "Linux",
        "ClientVersion": "0",
        "PropagationChannelId": "FFFFFFFFFFFFFFFF",
        "SponsorId": "FFFFFFFFFFFFFFFF",
        "RemoteServerListURLs": [
            {
                "URL": base64.b64encode(SERVER_LIST_URL.encode()).decode(),
                "OnlyAfterAttempts": 0,
                "SkipVerify": False,
            }
        ],
        "RemoteServerListSignaturePublicKey": SERVER_LIST_PUBLIC_KEY,
    }
