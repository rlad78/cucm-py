# CUCM Toolkit

## Background

This project is a fork of PresidioCode's [ciscoaxl](https://github.com/PresidioCode/ciscoaxl) Python module that aims to address a lot of the issues/annoyances found over its use. The main goals for improving the AXL SDK are the following:

- [x] Serialize and sanatize returned data in a standard dict-like typing
- [ ] Automate SOAP structure analysis to handle signature verfication on all requests **for all API versions**
- [x] Verify CUCM and AXL API connection and authentification and give detailed error descriptions
- [ ] Supply proper arguments for public user methods instead of accepting args/kwargs
- [ ] Write throrough docstrings for all public methods

This project also aims to integrate [RisPort](https://developer.cisco.com/docs/sxml/#!risport70-api-reference/risport70-api-reference) and [UDS](https://developer.cisco.com/site/user-data-services/documents/developer-guide/) SDKs, as well as provide valuable functionality by leveraging the capabilities of all three APIs. This coulde include functionality such as:

- Getting IP addresses of devices matching a certain criteria
- Listing devices that haven't connected to a UCM server within a certain period of time
- (will think of more later...)

Please note that this is **still very early in development** and at this time, it is still recommended to use the original [ciscoaxl](https://github.com/PresidioCode/ciscoaxl) module.