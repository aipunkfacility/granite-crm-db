# tests/test_utils.py
import pytest
from granite.utils import normalize_phone, normalize_phones, extract_emails, \
    compare_names, extract_street, extract_domain, pick_best_value, is_safe_url, is_safe_link_url, \
    slugify, sanitize_filename


class TestNormalizePhone:
    def test_full_format_plus7(self):
        assert normalize_phone("+79031234567") == "79031234567"

    def test_full_format_8(self):
        assert normalize_phone("89031234567") == "79031234567"

    def test_with_spaces(self):
        assert normalize_phone("+7 (903) 123-45-67") == "79031234567"

    def test_short_format_10_digits(self):
        assert normalize_phone("9031234567") == "79031234567"

    def test_invalid_empty(self):
        assert normalize_phone("") is None

    def test_invalid_letters(self):
        assert normalize_phone("abc") is None

    def test_invalid_too_short(self):
        assert normalize_phone("123") is None

    def test_normalize_phones_dedup(self):
        result = normalize_phones(["+79031234567", "89031234567", "79031234567"])
        assert result == ["79031234567"]


class TestExtractEmails:
    def test_single(self):
        assert extract_emails("Contact: info@site.ru") == ["info@site.ru"]

    def test_multiple(self):
        result = extract_emails("Email: a@b.com and test@c.ru")
        assert result == ["a@b.com", "test@c.ru"]

    def test_none_input(self):
        assert extract_emails(None) == []

    def test_no_emails(self):
        assert extract_emails("No emails here") == []


class TestCompareNames:
    def test_exact_match(self):
        assert compare_names("Гранит-Мастер", "Гранит-Мастер") is True

    def test_case_insensitive(self):
        assert compare_names("Гранит-Мастер", "гранит-мастер") is True

    def test_reversed_words(self):
        assert compare_names("Гранит-Мастер Иванов", "Иванов Гранит-Мастер", 85) is True

    def test_different_companies(self):
        assert compare_names("Гранит-Мастер", "Мир Камня", 88) is False

    def test_empty(self):
        assert compare_names("", "Гранит-Мастер") is False


class TestExtractDomain:
    def test_simple(self):
        assert extract_domain("https://site.ru/page") == "site.ru"

    def test_www(self):
        assert extract_domain("www.site.ru") == "site.ru"

    def test_none(self):
        assert extract_domain(None) is None


class TestPickBestValue:
    def test_longest(self):
        assert pick_best_value("коротко", "среднее значение", "самое длинное значение") \
            == "самое длинное значение"

    def test_empty(self):
        assert pick_best_value("", None) == ""


class TestIsSafeUrl:
    """Tests for SSRF protection in is_safe_url()."""

    # --- Should be SAFE (return True) ---

    def test_normal_https(self):
        assert is_safe_url("https://example.com") is True

    def test_normal_http(self):
        assert is_safe_url("http://example.com/path?q=1") is True

    def test_with_port(self):
        assert is_safe_url("https://example.com:443/page") is True

    def test_public_ip(self):
        assert is_safe_url("https://8.8.8.8") is True

    def test_public_ip_1_1_1_1(self):
        assert is_safe_url("https://1.1.1.1") is True

    # --- Should be BLOCKED (return False) ---

    def test_none(self):
        assert is_safe_url(None) is False

    def test_empty(self):
        assert is_safe_url("") is False

    def test_non_string(self):
        assert is_safe_url(123) is False

    def test_ftp_scheme(self):
        assert is_safe_url("ftp://example.com") is False

    def test_javascript_scheme(self):
        assert is_safe_url("javascript:alert(1)") is False

    def test_no_scheme(self):
        assert is_safe_url("example.com") is False

    def test_localhost(self):
        assert is_safe_url("http://localhost") is False

    def test_localhost_with_port(self):
        assert is_safe_url("http://localhost:8080/api") is False

    def test_127_0_0_1(self):
        assert is_safe_url("http://127.0.0.1") is False

    def test_127_0_0_1_with_path(self):
        assert is_safe_url("http://127.0.0.1/admin") is False

    def test_10_private(self):
        assert is_safe_url("http://10.0.0.1") is False

    def test_192_168_private(self):
        assert is_safe_url("http://192.168.1.1") is False

    def test_169_254_metadata(self):
        assert is_safe_url("http://169.254.169.254/latest/meta-data/") is False

    def test_172_16_private(self):
        assert is_safe_url("http://172.16.0.1") is False

    def test_172_31_private(self):
        assert is_safe_url("http://172.31.255.255") is False

    def test_172_15_not_private(self):
        """172.15.x.x is NOT private (only 16-31)."""
        assert is_safe_url("http://172.15.0.1") is True

    def test_0_0_0_0(self):
        assert is_safe_url("http://0.0.0.0") is False

    def test_metadata_google_internal(self):
        assert is_safe_url("http://metadata.google.internal") is False

    # --- CGNAT (100.64.0.0/10) — previously unblocked ---

    def test_cgnat_100_64_0_1(self):
        assert is_safe_url("http://100.64.0.1") is False

    def test_cgnat_100_127_255_255(self):
        assert is_safe_url("http://100.127.255.255") is False

    def test_cgnat_boundary_100_63(self):
        """100.63.x.x is NOT CGNAT."""
        assert is_safe_url("http://100.63.255.255") is True

    def test_cgnat_boundary_100_128(self):
        """100.128.x.x is NOT CGNAT."""
        assert is_safe_url("http://100.128.0.1") is True

    # --- IPv6 ---

    def test_ipv6_loopback(self):
        assert is_safe_url("http://[::1]") is False

    def test_ipv6_unspecified(self):
        assert is_safe_url("http://[::]") is False

    def test_ipv6_ula_fd00(self):
        assert is_safe_url("http://[fd12:3456::1]") is False

    def test_ipv6_link_local_fe80(self):
        assert is_safe_url("http://[fe80::1]") is False

    def test_ipv6_mapped_ipv4_loopback(self):
        """::ffff:127.0.0.1 maps to 127.0.0.1 — must be blocked."""
        assert is_safe_url("http://[::ffff:127.0.0.1]") is False

    # --- Edge cases ---

    def test_null_byte_in_url(self):
        """Null byte is stripped by is_safe_url, resulting in a safe URL."""
        # is_safe_url strips \x00 via re.sub(r'[\s\x00]+', '', url),
        # yielding "http://example.com.evil.com" which is a safe public domain.
        result = is_safe_url("http://example.com\x00.evil.com")
        assert result is True

    def test_whitespace_stripped(self):
        assert is_safe_url("  http://example.com  ") is True

    def test_newline_in_url(self):
        """Newline is stripped by is_safe_url, resulting in a safe URL."""
        # is_safe_url strips \n via re.sub(r'[\s\x00]+', '', url),
        # yielding "http://example.com.evil.com" which is a safe public domain.
        result = is_safe_url("http://example.com\n.evil.com")
        assert result is True


class TestExtractStreet:
    """Tests for extract_street() — basic street extraction from addresses."""

    def test_full_address_with_ul(self):
        assert extract_street("г. Новосибирск, ул. Ленина, 45") == "ленина"

    def test_address_with_prospect(self):
        assert extract_street("Новосибирск, проспект Маркса 12") == "маркса"

    def test_address_with_city_prefix(self):
        """City prefix (г.) is stripped before extraction."""
        result = extract_street("г. Волгоград, ул. Ленина, 10")
        assert result == "ленина"

    def test_empty_string(self):
        assert extract_street("") == ""

    def test_address_without_street_keyword(self):
        """Without a street keyword and no comma, returns the whole string."""
        result = extract_street("какой-то текст")
        assert result == "какой-то текст"

    def test_address_without_street_keyword_but_comma(self):
        """Without a street keyword, returns the part before the first comma."""
        result = extract_street("Район Центральный, дом 5")
        assert result == "район центральный"


class TestSlugify:
    """Tests for slugify() — Cyrillic-to-Latin transliteration for URLs."""

    def test_russian_city_name(self):
        assert slugify("Волгоград") == "volgograd"

    def test_name_with_hyphen(self):
        assert slugify("Санкт-Петербург") == "sankt-peterburg"

    def test_empty_string(self):
        assert slugify("") == ""

    def test_two_words(self):
        assert slugify("Новый Уренгой") == "novyy-urengoy"


class TestSanitizeFilename:
    """Tests for sanitize_filename() — safe filename generation."""

    def test_normal_name_unchanged(self):
        assert sanitize_filename("report") == "report"

    def test_special_chars_sanitized(self):
        assert sanitize_filename("path/file\\name") == "path_file_name"

    def test_empty_string_returns_unnamed(self):
        assert sanitize_filename("") == "unnamed"

    def test_spaces_replaced(self):
        assert sanitize_filename("my report file") == "my_report_file"

    def test_leading_trailing_underscores_stripped(self):
        assert sanitize_filename("__test__") == "test"


class TestIsSafeLinkUrl:
    """Tests for is_safe_link_url() — markdown/href safety."""

    def test_http_allowed(self):
        assert is_safe_link_url("https://example.com") is True

    def test_javascript_blocked(self):
        assert is_safe_link_url("javascript:alert(1)") is False

    def test_data_uri_blocked(self):
        assert is_safe_link_url("data:text/html,<script>") is False

    def test_vbscript_blocked(self):
        assert is_safe_link_url("vbscript:run") is False

    def test_empty_blocked(self):
        assert is_safe_link_url("") is False

    def test_no_hostname_blocked(self):
        assert is_safe_link_url("http://") is False
