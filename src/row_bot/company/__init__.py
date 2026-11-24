"""Row-Bot for Teams on this computer: joining a company's Row-Bot server (C1).

- ``store``: what a joined Row-Bot keeps (a small record in the data folder, its credential in the keychain).
- ``client``: the one HTTP client to the company's server (https, no cookies, one refresh at a time).
- ``sign_in``: the company sign-in in this computer's browser, back to a loopback address (PKCE).
- ``provider``: the company's models as the ``company`` provider (its catalog, rows and readiness, C2).
- ``gateway``: the calls to those models through the company's gateway, with the device's bearer (C2).
- ``policy``: the company's policy in this app: fetched, cached, the offline lease and lockdown, and the models
  it allows (C3a).
- ``enforce``: the rest of it at the app's seams: tools switched off, the approval ceiling, which apps may be
  added and run, and chat export (C3b).
- ``audit``: what this Row-Bot tells the company, metadata only: gated tool actions, exports and ended lockdowns,
  kept in a bounded outbox and sent in batches (C4).
- ``managed``: the company server this computer's IT department set (a registry, managed-preferences or
  root-owned file setting, C5): Join goes only there and Leave isn't offered.
- ``brain``: the company's knowledge graph, read in Knowledge home (B8).
- ``recall`` and ``brain_tool``: the company brain in a chat, the ``company_brain_search`` tool and the company's
  hits in a turn's recall (B3); ``documents``: a space owner's company documents (B3).

With no company joined nothing here runs: reading the state looks for the record file and imports neither
``client`` nor ``sign_in``, so no socket is opened and no thread is started.
"""
