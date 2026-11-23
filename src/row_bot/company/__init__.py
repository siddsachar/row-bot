"""Row-Bot for Teams on this computer: joining a company's Row-Bot server (C1).

- ``store``: what a joined Row-Bot keeps (a small record in the data folder, its credential in the keychain).
- ``client``: the one HTTP client to the company's server (https, no cookies, one refresh at a time).
- ``sign_in``: the company sign-in in this computer's browser, back to a loopback address (PKCE).
- ``provider``: the company's models as the ``company`` provider (its catalog, rows and readiness, C2).
- ``gateway``: the calls to those models through the company's gateway, with the device's bearer (C2).
- ``policy``: what the company's policy changes in this app (C3 fills it in).

With no company joined nothing here runs: reading the state looks for the record file and imports neither
``client`` nor ``sign_in``, so no socket is opened and no thread is started.
"""
