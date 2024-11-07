from enum import Enum

class AzureLocationEnum(Enum):
  #region Americas
  USWest              = 'westus'
  USWest2             = 'westus2'
  USCentral           = 'centralus'
  USEast              = 'eastus'
  USEast2             = 'eastus2'
  USNorthCentral      = 'northcentralus'
  USSouthCentral      = 'southcentralus'
  USWestCentral       = 'westcentralus'
  CanadaCentral       = 'canadacentral'
  CanadaEast          = 'canadaeast'
  BrazilSouth         = 'brazilsouth'
  #endregion
  
  #region Europe
  EuropeNorth         = 'northeurope'
  EuropeWest          = 'westeurope'
  UKSouth             = 'uksouth'
  UKWest              = 'ukwest'
  FranceCentral       = 'francecentral'
  FranceSouth         = 'francesouth'
  SwitzerlandNorth    = 'switzerlandnorth'
  SwitzerlandWest     = 'switzerlandwest'
  GermanyNorth        = 'germanynorth'
  GermanyWestCentral  = 'germanywestcentral'
  NorwayWest          = 'norwaywest'
  NorwayEast          = 'norwayeast'
  #endregion
  
  #region Asia
  AsiaEast            = 'eastasia'
  AsiaSouthEast       = 'southeastasia'
  JapanEast           = 'japaneast'
  JapanWest           = 'japanwest'
  AustraliaEast       = 'australiaeast'
  AustraliaSouthEast  = 'australiasoutheast'
  AustraliaCentral    = 'australiacentral'
  AustraliaCentral2   = 'australiacentral2'
  IndiaCentral        = 'centralindia'
  IndiaSouth          = 'southindia'
  IndiaWest           = 'westindia'
  KoreaSouth          = 'koreasouth'
  KoreaCentral        = 'koreacentral'
  #endregion
  
  #region Middle East and Africa
  UAECentral          = 'uaecentral'
  UAENorth            = 'uaenorth'
  SouthAfricaNorth    = 'southafricanorth'
  SouthAfricaWest     = 'southafricawest'
  #endregion
  
  #region China
  ChinaNorth          = 'chinanorth'
  ChinaEast           = 'chinaeast'
  ChinaNorth2         = 'chinanorth2'
  ChinaEast2          = 'chinaeast2'
  #endregion
  
  #region German
  GermanyCentral      = 'germanycentral'
  GermanyNorthEast    = 'germanynortheast'
  #endregion