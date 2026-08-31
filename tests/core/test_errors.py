import pytest

from retro_roster_patcher.core.errors import (
    ApiError,
    CapabilityError,
    MappingError,
    RetroRosterError,
    RomError,
)


@pytest.mark.parametrize("cls", [RomError, ApiError, MappingError, CapabilityError])
def test_every_error_is_a_retro_roster_error(cls):
    assert issubclass(cls, RetroRosterError)


def test_base_error_is_an_exception():
    assert issubclass(RetroRosterError, Exception)


def test_errors_carry_their_message():
    err = RomError("Invalid NHL94 Genesis ROM")
    assert str(err) == "Invalid NHL94 Genesis ROM"
