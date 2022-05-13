# cucm-py

> Please note that this is **still very early in development** and at this time and it is still recommended to use the original [ciscoaxl](https://github.com/PresidioCode/ciscoaxl) module.

## Background

This project is a fork of PresidioCode's [ciscoaxl](https://github.com/PresidioCode/ciscoaxl) Python module that aims to expand it usability with the following features:

- [x] Serialize and sanatize returned data in a standard dict-like typing
- [x] Automate SOAP structure analysis to handle signature verfication on all requests **for all API versions**
- [x] Verify CUCM and AXL API connection/authentification and give detailed error descriptions
- [ ] Write throrough docstrings for all public methods

This project also aims to integrate [RisPort](https://developer.cisco.com/docs/sxml/#!risport70-api-reference/risport70-api-reference) and [CUPI](https://www.cisco.com/c/en/us/td/docs/voice_ip_comm/connection/REST-API/CUPI_API/b_CUPI-API/b_CUPI-API_chapter_01.html) (Cisco Unity Provisioning Interface) SDKs, as well as provide valuable functionality by leveraging the capabilities of all three APIs. All 3 APIs will have their own classes that can be interfaced with individually for the user to create their own tooling with.
