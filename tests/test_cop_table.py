"""COP table entry accepts locale numbers and preserves the chosen row count."""

import unittest

from custom_components.ha_bems.cop_table import default_table_text, parse_table_text


class COPTableTests(unittest.TestCase):
    def test_four_reference_defaults_and_all_supported_counts(self):
        self.assertEqual(default_table_text(4), '-7;2.32\n2;3.12\n7;4.11\n12;4.61')
        for count in range(2, 11):
            points = parse_table_text(default_table_text(count), count)
            self.assertEqual(len(points), count)
            self.assertEqual(points[0], {'temperature_c': -7, 'cop': 2.32})
            self.assertEqual(points[-1], {'temperature_c': 12, 'cop': 4.61})

    def test_decimal_comma_dot_whitespace_and_sorting(self):
        points = parse_table_text('  +02 ; 3,12 \n\n -07;2.32\r\n', 2)
        self.assertEqual(points, [{'temperature_c': -7, 'cop': 2.32}, {'temperature_c': 2, 'cop': 3.12}])

    def test_invalid_rows_numbers_and_count(self):
        for text in ('-7;2', '-7;2\n2;3\n7;4', '-7,2\n2,3', '-7;2;3\n2;3',
                     '-7;2\n-7;3', '-7;0\n2;3', '-7;-2\n2;3',
                     '-7;nan\n2;3', '-inf;2\n2;3', '-7;hello\n2;3'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_table_text(text, 2)

    def test_saved_points_preserve_precision_and_resize_using_their_curve(self):
        saved = [{'temperature_c': 0, 'cop': 2.123456789012345}, {'temperature_c': 10, 'cop': 4}]
        self.assertEqual(parse_table_text(default_table_text(2, saved), 2), saved)
        points = parse_table_text(default_table_text(3, saved), 3)
        self.assertEqual(points[1]['temperature_c'], 5)
        self.assertAlmostEqual(points[1]['cop'], (saved[0]['cop'] + 4) / 2)
