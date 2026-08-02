//+------------------------------------------------------------------+
//|                                  Domain/AccountValidator.mqh     |
//|  Single-account terminal validation for order commands.          |
//+------------------------------------------------------------------+
#property strict

#include "PlatformApi.mqh"

//+------------------------------------------------------------------+
//| AccountValidator — validates the 'account' field of order        |
//| commands against this terminal's logged-in broker account.       |
//|                                                                  |
//| MetaTrader supports exactly one account per terminal instance,   |
//| so the command's account must equal the broker login ID.         |
//| There is no silent fallback and no account name in the EA config.|
//+------------------------------------------------------------------+
class AccountValidator
{
public:
   //--- Returns true when the command may trade on this terminal.
   //--- resolvedAccount receives the broker login ID echoed in fills
   //--- and position_sync entries.
   static bool Validate(IAccountApi *accountApi, string payloadAccount, string &resolvedAccount)
   {
      resolvedAccount = IntegerToString(accountApi.Login());

      if(StringLen(payloadAccount) == 0)
         return false; // the app always sends an account with order commands

      return payloadAccount == resolvedAccount;
   }
};
