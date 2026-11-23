"""Row-Bot for Teams on this computer: joining a company's Row-Bot server (C1).

- ``store``: what a joined Row-Bot keeps (a small record in the data folder, its credential in the keychain).
- ``client``: the one HTTP client to the company's server (https, no cookies, one refresh at a time).
- ``sign_in``: the company sign-in in this computer's browser, back to a loopback address (PKCE).

With no company joined nothing here runs: reading the state looks for the record file and imports neither
``client`` nor ``sign_in``, so no socket is opened and no thread is started.
"""
