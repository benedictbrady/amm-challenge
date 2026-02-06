"""Static analysis validator for Solidity strategies."""

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationResult:
    """Result of Solidity validation."""

    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class SolidityValidator:
    """Static analysis validator for user-submitted Solidity strategies.

    Ensures strategies:
    - Inherit from AMMStrategyBase
    - Define required functions (afterInitialize, afterSwap, getName)
    - Don't use dangerous patterns (external calls, assembly, selfdestruct, etc.)
    """

    # Dangerous patterns that are blocked
    BLOCKED_PATTERNS = [
        # External calls
        (r"\bcall\s*\{", "External calls (call{) are not allowed"),
        (r"\bdelegatecall\s*\(", "delegatecall is not allowed"),
        (r"\bstaticcall\s*\(", "staticcall is not allowed"),
        # Dangerous operations
        (r"\bselfdestruct\s*\(", "selfdestruct is not allowed"),
        (r"\bsuicide\s*\(", "suicide is not allowed"),
        # Assembly (could bypass restrictions)
        (r"\bassembly\s*\{", "Inline assembly is not allowed"),
        # Creating other contracts
        (r"\bnew\s+\w+\s*\(", "Creating new contracts is not allowed"),
        # Low-level address calls
        (r"\.transfer\s*\(", "transfer() is not allowed"),
        (r"\.send\s*\(", "send() is not allowed"),
        # Block manipulation hints
        (r"\bcoinbase\b", "block.coinbase access is not allowed"),
        # External contract interactions
        (r"interface\s+\w+\s*\{(?![\s\S]*IAMMStrategy)", "Custom interfaces are not allowed"),
    ]

    # Required patterns
    REQUIRED_PATTERNS = [
        # Must inherit from AMMStrategyBase
        (
            r"contract\s+Strategy\s+is\s+AMMStrategyBase",
            "Contract must be named 'Strategy' and inherit from AMMStrategyBase",
        ),
        # Must implement afterInitialize
        (
            r"function\s+afterInitialize\s*\(",
            "Must implement afterInitialize(uint256, uint256) function",
        ),
        # Must implement afterSwap
        (
            r"function\s+afterSwap\s*\(",
            "Must implement afterSwap(TradeInfo calldata) function",
        ),
        # Must implement getName
        (
            r"function\s+getName\s*\(",
            "Must implement getName() function",
        ),
    ]

    # Allowed imports (only base contracts)
    ALLOWED_IMPORTS = [
        r'"./AMMStrategyBase\.sol"',
        r'"./IAMMStrategy\.sol"',
        r'"\./AMMStrategyBase\.sol"',
        r'"\./IAMMStrategy\.sol"',
        # With curly braces
        r"\{AMMStrategyBase\}\s+from\s+",
        r"\{IAMMStrategy,\s*TradeInfo\}\s+from\s+",
        r"\{TradeInfo\}\s+from\s+",
    ]

    def validate(self, source_code: str) -> ValidationResult:
        """Validate Solidity source code.

        Args:
            source_code: The Solidity source code to validate

        Returns:
            ValidationResult with valid flag and any errors/warnings
        """
        errors: list[str] = []
        warnings: list[str] = []

        # Check for required pragma
        if not re.search(r"pragma\s+solidity\s+", source_code):
            errors.append("Missing pragma solidity directive")

        # Check SPDX license identifier (warning only)
        if not re.search(r"//\s*SPDX-License-Identifier:", source_code):
            warnings.append("Missing SPDX license identifier")

        # Check for blocked patterns
        for pattern, message in self.BLOCKED_PATTERNS:
            if re.search(pattern, source_code, re.IGNORECASE):
                errors.append(message)

        # Check for required patterns
        for pattern, message in self.REQUIRED_PATTERNS:
            if not re.search(pattern, source_code):
                errors.append(message)

        # Validate imports
        import_errors = self._validate_imports(source_code)
        errors.extend(import_errors)

        # Check for storage outside of slots array
        storage_warnings = self._check_storage_usage(source_code)
        warnings.extend(storage_warnings)

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def _validate_imports(self, source_code: str) -> list[str]:
        """Validate that only allowed imports are used.

        Args:
            source_code: The source code to check

        Returns:
            List of error messages for invalid imports
        """
        errors = []

        # Find all import statements
        import_pattern = r'import\s+(?:[\{][\w\s,]+[\}]\s+from\s+)?["\']([^"\']+)["\']'
        imports = re.findall(import_pattern, source_code)

        for import_path in imports:
            # Check if this import is allowed
            allowed = False
            for allowed_pattern in self.ALLOWED_IMPORTS:
                if re.search(allowed_pattern, f'"{import_path}"'):
                    allowed = True
                    break

            if not allowed:
                # Check if it's importing from the base contracts
                if "AMMStrategyBase" in import_path or "IAMMStrategy" in import_path:
                    allowed = True
                else:
                    errors.append(
                        f"Import '{import_path}' is not allowed. "
                        "Only AMMStrategyBase and IAMMStrategy can be imported."
                    )

        return errors

    def _check_storage_usage(self, source_code: str) -> list[str]:
        """Check for potential storage variables outside the slots array.

        This is a heuristic check - the actual enforcement is at the EVM level.

        Args:
            source_code: The source code to check

        Returns:
            List of warning messages
        """
        warnings = []

        # Look for state variable declarations (outside function bodies)
        # This is a simple heuristic - not perfect but catches common cases

        # Pattern for state variable declarations
        # Matches things like: uint256 myVar; or mapping(...) myMap;
        state_var_pattern = r"^\s*(uint\d*|int\d*|bool|address|bytes\d*|string|mapping\s*\([^)]+\))\s+(?!constant|immutable)(\w+)\s*[;=]"

        # Find the contract body
        contract_match = re.search(r"contract\s+Strategy\s+is\s+AMMStrategyBase\s*\{", source_code)
        if contract_match:
            # Get content after contract declaration
            contract_body = source_code[contract_match.end() :]

            # Remove function bodies to only check contract-level declarations
            # This is a simplification - proper parsing would require a Solidity parser
            depth = 1
            contract_level_code = ""
            i = 0
            while i < len(contract_body) and depth > 0:
                char = contract_body[i]
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                elif depth == 1:
                    contract_level_code += char
                i += 1

            # Check for state variables
            for line in contract_level_code.split("\n"):
                match = re.match(state_var_pattern, line)
                if match:
                    var_name = match.group(2)
                    # Ignore known safe patterns
                    if var_name not in ["slots", "WAD", "MAX_FEE", "MIN_FEE", "BPS"]:
                        warnings.append(
                            f"State variable '{var_name}' declared outside slots array. "
                            "Use slots[0-31] for persistent storage to ensure storage limits."
                        )

        return warnings

    def quick_check(self, source_code: str) -> tuple[bool, Optional[str]]:
        """Quick validation check for basic requirements.

        Args:
            source_code: The source code to check

        Returns:
            Tuple of (is_valid, error_message)
        """
        result = self.validate(source_code)
        if result.valid:
            return True, None
        return False, result.errors[0] if result.errors else "Unknown validation error"
