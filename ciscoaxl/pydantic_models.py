from pydantic import BaseModel as PydanticBaseModel
from pydantic import Field
from typing import Any, Union, List


class BaseModel(PydanticBaseModel):
    class Config:
        allow_population_by_field_name = True
        underscore_attrs_are_private = False


class XFkType(BaseModel):
    _value_1: str
    uuid: str


class XFkType(BaseModel):
	_value_1: str
	uuid: str


class XLoadInformation(BaseModel):
	_value_1: str
	special: str


class RDirn(BaseModel):
	pattern: str
	routePartitionName: XFkType
	uuid: str


class callInfoDisplay(BaseModel):
	callerName: str
	callerNumber: str
	redirectedNumber: str
	dialedNumber: str


class REnduserMember(BaseModel):
	userId: str


class associatedEndusers(BaseModel):
	enduser: List[REnduserMember]


class RPhoneLine(BaseModel):
	index: Union[int, float, str]
	label: str
	display: str
	dirn: RDirn
	ringSetting: str
	consecutiveRingSetting: str
	ringSettingIdlePickupAlert: str
	ringSettingActivePickupAlert: str
	displayAscii: str
	e164Mask: str
	dialPlanWizardId: Union[int, float, str]
	mwlPolicy: str
	maxNumCalls: Union[int, float, str]
	busyTrigger: Union[int, float, str]
	callInfoDisplay: callInfoDisplay
	recordingProfileName: XFkType
	monitoringCssName: XFkType
	recordingFlag: str
	audibleMwi: str
	speedDial: str
	partitionUsage: str
	associatedEndusers: associatedEndusers
	missedCallLogging: str
	recordingMediaSource: str
	ctiid: Union[int, float, str]
	uuid: str


class RNumplanIdentifier(BaseModel):
	directoryNumber: str
	routePartitionName: str


class lines(BaseModel):
	line: List[RPhoneLine]
	lineIdentifier: List[RNumplanIdentifier]


class RSpeeddial(BaseModel):
	dirn: str
	label: str
	index: Union[int, float, str]


class speeddials(BaseModel):
	speeddial: List[RSpeeddial]


class associatedBlfSdFeatures(BaseModel):
	feature: List[str]


class RBusyLampField(BaseModel):
	blfDest: str
	blfDirn: str
	routePartition: str
	label: str
	associatedBlfSdFeatures: associatedBlfSdFeatures
	index: Union[int, float, str]


class busyLampFields(BaseModel):
	busyLampField: List[RBusyLampField]


class directedCallParkDnAndPartition(BaseModel):
	dnPattern: str
	routePartitionName: XFkType


class RBLFDirectedCallPark(BaseModel):
	label: str
	directedCallParkId: str
	directedCallParkDnAndPartition: directedCallParkDnAndPartition
	index: Union[int, float, str]


class blfDirectedCallParks(BaseModel):
	blfDirectedCallPark: List[RBLFDirectedCallPark]


class RAddOnModule(BaseModel):
	loadInformation: XLoadInformation
	model: str
	index: Union[int, float, str]
	uuid: str


class addOnModules(BaseModel):
	addOnModule: List[RAddOnModule]


class RSubscribedService(BaseModel):
	telecasterServiceName: XFkType
	name: str
	url: str
	urlButtonIndex: Union[int, float, str]
	urlLabel: str
	serviceNameAscii: str
	phoneService: str
	phoneServiceCategory: str
	vendor: str
	version: str
	priority: Union[int, float, str]
	uuid: str


class services(BaseModel):
	service: List[RSubscribedService]


class currentConfig(BaseModel):
	userHoldMohAudioSourceId: Any
	phoneTemplateName: XFkType
	mlppDomainId: str
	mlppIndicationStatus: str
	preemption: str
	softkeyTemplateName: XFkType
	ignorePresentationIndicators: str
	singleButtonBarge: str
	joinAcrossLines: str
	callInfoPrivacyStatus: str
	dndStatus: str
	dndRingSetting: str
	dndOption: str
	alwaysUsePrimeLine: str
	alwaysUsePrimeLineForVoiceMessage: str
	emccCallingSearchSpaceName: XFkType
	deviceName: str
	model: str
	product: str
	deviceProtocol: str
	class_field: str = Field(alias='class')
	addressMode: str
	allowAutoConfig: str
	remoteSrstOption: str
	remoteSrstIp: str
	remoteSrstPort: Union[int, float, str]
	remoteSipSrstIp: str
	remoteSipSrstPort: Union[int, float, str]
	geolocationInfo: str
	remoteLocationName: str


class confidentialAccess(BaseModel):
	confidentialAccessMode: str
	confidentialAccessLevel: Union[int, float, str]


class RPhone(BaseModel):
	name: str
	description: str
	product: str
	model: str
	class_field: str = Field(alias='class')
	protocol: str
	protocolSide: str
	callingSearchSpaceName: XFkType
	devicePoolName: XFkType
	commonDeviceConfigName: XFkType
	commonPhoneConfigName: XFkType
	networkLocation: str
	locationName: XFkType
	mediaResourceListName: XFkType
	networkHoldMohAudioSourceId: Any
	userHoldMohAudioSourceId: Any
	automatedAlternateRoutingCssName: XFkType
	aarNeighborhoodName: XFkType
	loadInformation: XLoadInformation
	vendorConfig: Any
	versionStamp: str
	traceFlag: str
	mlppDomainId: str
	mlppIndicationStatus: str
	preemption: str
	useTrustedRelayPoint: str
	retryVideoCallAsAudio: str
	securityProfileName: XFkType
	sipProfileName: XFkType
	cgpnTransformationCssName: XFkType
	useDevicePoolCgpnTransformCss: str
	geoLocationName: XFkType
	geoLocationFilterName: XFkType
	sendGeoLocation: str
	lines: lines
	numberOfButtons: Union[int, float, str]
	phoneTemplateName: XFkType
	speeddials: speeddials
	busyLampFields: busyLampFields
	primaryPhoneName: XFkType
	ringSettingIdleBlfAudibleAlert: str
	ringSettingBusyBlfAudibleAlert: str
	blfDirectedCallParks: blfDirectedCallParks
	addOnModules: addOnModules
	userLocale: str
	networkLocale: str
	idleTimeout: Union[int, float, str]
	authenticationUrl: str
	directoryUrl: str
	idleUrl: str
	informationUrl: str
	messagesUrl: str
	proxyServerUrl: str
	servicesUrl: str
	services: services
	softkeyTemplateName: XFkType
	loginUserId: str
	defaultProfileName: XFkType
	enableExtensionMobility: str
	currentProfileName: XFkType
	loginTime: Union[int, float, str]
	loginDuration: Union[int, float, str]
	currentConfig: currentConfig
	singleButtonBarge: str
	joinAcrossLines: str
	builtInBridgeStatus: str
	callInfoPrivacyStatus: str
	hlogStatus: str
	ownerUserName: XFkType
	ignorePresentationIndicators: str
	packetCaptureMode: str
	packetCaptureDuration: Union[int, float, str]
	subscribeCallingSearchSpaceName: XFkType
	rerouteCallingSearchSpaceName: XFkType
	allowCtiControlFlag: str
	presenceGroupName: XFkType
	unattendedPort: str
	requireDtmfReception: str
	rfc2833Disabled: str
	certificateOperation: str
	authenticationMode: str
	keySize: str
	keyOrder: str
	ecKeySize: str
	authenticationString: str
	certificateStatus: str
	upgradeFinishTime: str
	deviceMobilityMode: str
	roamingDevicePoolName: XFkType
	remoteDevice: str
	dndOption: str
	dndRingSetting: str
	dndStatus: str
	isActive: str
	isDualMode: str
	mobilityUserIdName: XFkType
	phoneSuite: str
	phoneServiceDisplay: str
	isProtected: str
	mtpRequired: str
	mtpPreferedCodec: str
	dialRulesName: XFkType
	sshUserId: str
	sshPwd: str
	digestUser: str
	outboundCallRollover: str
	hotlineDevice: str
	secureInformationUrl: str
	secureDirectoryUrl: str
	secureMessageUrl: str
	secureServicesUrl: str
	secureAuthenticationUrl: str
	secureIdleUrl: str
	alwaysUsePrimeLine: str
	alwaysUsePrimeLineForVoiceMessage: str
	featureControlPolicy: XFkType
	deviceTrustMode: str
	earlyOfferSupportForVoiceCall: str
	requireThirdPartyRegistration: str
	blockIncomingCallsWhenRoaming: str
	homeNetworkId: str
	AllowPresentationSharingUsingBfcp: str
	confidentialAccess: confidentialAccess
	requireOffPremiseLocation: str
	allowiXApplicableMedia: str
	cgpnIngressDN: XFkType
	useDevicePoolCgpnIngressDN: str
	msisdn: str
	enableCallRoutingToRdWhenNoneIsActive: str
	wifiHotspotProfile: XFkType
	wirelessLanProfileGroup: XFkType
	elinGroup: XFkType
	ctiid: Union[int, float, str]
	uuid: str
