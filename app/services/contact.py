"""
Contact Service
Business logic for contact management, validation, and CSV operations
"""
import re
import csv
import io
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# E.164 phone number regex: + followed by 1-15 digits
PHONE_REGEX = re.compile(r'^\+?[1-9]\d{1,14}$')
EMAIL_REGEX = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def validate_phone_number(phone: str) -> bool:
    """Validate phone number format (E.164)"""
    if not phone or not isinstance(phone, str):
        return False
    # Remove spaces and dashes for validation
    cleaned = phone.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
    return bool(PHONE_REGEX.match(cleaned))


def normalize_phone_number(phone: str) -> str:
    """Normalize phone number to E.164 format"""
    if not phone or not isinstance(phone, str):
        raise ValueError("Phone number must be a non-empty string")
    
    # Remove all non-digit characters except +
    cleaned = re.sub(r'[^\d+]', '', phone)
    
    # If it doesn't start with +, add it
    if not cleaned.startswith('+'):
        # If it starts with 1 (US), add +
        if cleaned.startswith('1') and len(cleaned) >= 11:
            cleaned = '+' + cleaned
        else:
            # Assume it's a US number without country code
            cleaned = '+1' + cleaned
    
    # Validate the normalized number
    if not PHONE_REGEX.match(cleaned):
        raise ValueError(f"Invalid phone number format: {phone}")
    
    return cleaned


def validate_email(email: str) -> bool:
    """Validate email format"""
    if not email or not isinstance(email, str):
        return False
    return bool(EMAIL_REGEX.match(email.strip()))


def validate_contact_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and normalize contact data
    
    Args:
        data: Contact data dictionary
        
    Returns:
        Validated and normalized contact data
        
    Raises:
        ValueError: If validation fails
    """
    errors = []
    
    # Validate required fields
    if 'phone_number' not in data or not data['phone_number']:
        errors.append("phone_number is required")
    else:
        try:
            data['phone_number'] = normalize_phone_number(data['phone_number'])
        except ValueError as e:
            errors.append(f"phone_number: {str(e)}")
    
    # Validate optional email
    if 'email' in data and data['email']:
        email = data['email'].strip()
        if not validate_email(email):
            errors.append(f"email: Invalid email format")
        else:
            data['email'] = email
    
    # Validate optional name fields
    if 'first_name' in data and data['first_name']:
        first_name = data['first_name'].strip()
        if len(first_name) > 50:
            errors.append("first_name: Maximum length is 50 characters")
        else:
            data['first_name'] = first_name
    
    if 'last_name' in data and data['last_name']:
        last_name = data['last_name'].strip()
        if len(last_name) > 50:
            errors.append("last_name: Maximum length is 50 characters")
        else:
            data['last_name'] = last_name
    
    # Validate metadata if present
    if 'metadata' in data and data['metadata']:
        if not isinstance(data['metadata'], dict):
            errors.append("metadata: Must be a dictionary/object")
    
    if errors:
        raise ValueError("; ".join(errors))
    
    return data


def map_contact_fields(
    raw_contact: Dict[str, Any], 
    mapping_config: Optional[Dict[str, str]] = None
) -> Dict[str, Any]:
    """
    Dynamic Mapper: Split standard fields from metadata using mapping config.
    
    Standard fields (promoted to columns):
    - phone_number (required)
    - email, first_name, last_name
    - company_name, industry, location, pin_code, keywords
    
    Everything else goes into metadata JSONB.
    
    Args:
        raw_contact: Raw contact data from CSV (keyed by CSV headers)
        mapping_config: Optional mapping from CSV headers to standard fields
                       Format: {'csv_header': 'standard_field'}
                       If None, uses auto-detection based on common patterns
    
    Returns:
        Dict with standard fields separated and metadata populated
    """
    # Define standard fields that get promoted to columns
    STANDARD_FIELDS = {
        'phone_number', 'email', 'first_name', 'last_name',
        'company_name', 'industry', 'location', 'pin_code', 'keywords'
    }
    
    # Normalize raw contact keys (lowercase, replace spaces with underscores)
    normalized_contact = {}
    for key, value in raw_contact.items():
        if value and str(value).strip():
            normalized_key = key.strip().lower().replace(' ', '_').replace('-', '_')
            normalized_contact[normalized_key] = str(value).strip()
    
    # Apply mapping config if provided
    mapped_contact = {}
    unmapped_headers = set(raw_contact.keys())  # Track unmapped headers
    
    if mapping_config:
        # Map from original CSV headers (not normalized) to standard fields
        for csv_header, standard_field in mapping_config.items():
            if not standard_field or standard_field == '':  # Skip if mapped to "Skip / Put in Metadata"
                continue
            # Check both original and normalized versions
            if csv_header in raw_contact:
                mapped_contact[standard_field] = raw_contact[csv_header]
                unmapped_headers.discard(csv_header)  # Mark as mapped
            else:
                # Try normalized version
                normalized_csv = csv_header.strip().lower().replace(' ', '_').replace('-', '_')
                if normalized_csv in normalized_contact:
                    mapped_contact[standard_field] = normalized_contact[normalized_csv]
                    # Find original header for this normalized key
                    for orig_key in raw_contact.keys():
                        if orig_key.strip().lower().replace(' ', '_').replace('-', '_') == normalized_csv:
                            unmapped_headers.discard(orig_key)
                            break
    else:
        # Auto-detect common patterns
        auto_mapping = {
            'phone': 'phone_number',
            'phone_number': 'phone_number',
            'mobile': 'phone_number',
            'mobile_number': 'phone_number',
            'firstname': 'first_name',
            'first_name': 'first_name',
            'fname': 'first_name',
            'lastname': 'last_name',
            'last_name': 'last_name',
            'lname': 'last_name',
            'email': 'email',
            'email_address': 'email',
            'company': 'company_name',
            'company_name': 'company_name',
            'industry': 'industry',
            'location': 'location',
            'city': 'location',
            'pin_code': 'pin_code',
            'pincode': 'pin_code',
            'postal_code': 'pin_code',
            'keywords': 'keywords',
            'tags': 'keywords',
        }
        
        # Map normalized keys to standard fields, preserve original headers for unmapped
        for orig_key, value in raw_contact.items():
            normalized_key = orig_key.strip().lower().replace(' ', '_').replace('-', '_')
            standard_field = auto_mapping.get(normalized_key)
            if standard_field:
                # Map to standard field
                mapped_contact[standard_field] = value
            else:
                # Keep original header name for metadata (not normalized)
                mapped_contact[orig_key] = value
    
    # Split into standard fields and metadata
    standard_data = {}
    metadata = {}
    
    for key, value in mapped_contact.items():
        if key in STANDARD_FIELDS:
            # Handle keywords as array if it's a comma-separated string
            if key == 'keywords' and isinstance(value, str):
                keywords_list = [k.strip() for k in value.split(',') if k.strip()]
                standard_data[key] = keywords_list if keywords_list else None
            else:
                standard_data[key] = value
        else:
            # Everything else goes to metadata
            metadata[key] = value
    
    # Add unmapped CSV columns to metadata (if mapping_config was provided)
    if mapping_config and unmapped_headers:
        for unmapped_header in unmapped_headers:
            if unmapped_header in raw_contact and raw_contact[unmapped_header]:
                # Use original header name as metadata key
                metadata[unmapped_header] = raw_contact[unmapped_header]
    
    # Ensure phone_number exists (required)
    if 'phone_number' not in standard_data:
        raise ValueError("phone_number is required")
    
    # Set metadata if there are custom fields
    if metadata:
        standard_data['metadata'] = metadata
    
    return standard_data


def parse_csv_contacts(file_content: str, mapping_config: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """
    Parse CSV file content into list of contact dictionaries with dynamic field mapping.
    
    Uses map_contact_fields to split standard fields from metadata JSONB.
    
    Args:
        file_content: CSV file content as string
        mapping_config: Optional mapping from CSV headers to standard fields
        
    Returns:
        List of contact dictionaries with standard fields and metadata separated
        
    Raises:
        ValueError: If CSV parsing fails or required columns are missing
    """
    try:
        # Create StringIO object from file content
        csv_file = io.StringIO(file_content)
        
        # Try to detect delimiter
        sample = csv_file.read(1024)
        csv_file.seek(0)
        sniffer = csv.Sniffer()
        delimiter = sniffer.sniff(sample).delimiter
        
        # Read CSV
        reader = csv.DictReader(csv_file, delimiter=delimiter)
        
        contacts = []
        
        # Check if CSV has headers
        if not reader.fieldnames:
            raise ValueError("CSV file is empty or invalid")
        
        # Parse rows
        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            try:
                # Keep original CSV headers for mapping
                raw_contact = {}
                for key, value in row.items():
                    if value and str(value).strip():
                        raw_contact[key] = str(value).strip()
                
                # Use dynamic mapper to split standard fields from metadata
                mapped_contact = map_contact_fields(raw_contact, mapping_config)
                
                # Validate and normalize contact data
                validated_contact = validate_contact_data(mapped_contact)
                contacts.append(validated_contact)
                
            except ValueError as e:
                logger.warning(f"Row {row_num} skipped: {str(e)}")
                continue
            except Exception as e:
                logger.warning(f"Row {row_num} skipped due to error: {str(e)}")
                continue
        
        if not contacts:
            raise ValueError("No valid contacts found in CSV file")
        
        return contacts
        
    except csv.Error as e:
        raise ValueError(f"CSV parsing error: {str(e)}")
    except Exception as e:
        raise ValueError(f"Error parsing CSV: {str(e)}")


def generate_csv_contacts(contacts: List[Dict[str, Any]]) -> str:
    """
    Generate CSV content from list of contacts including standard fields and metadata.
    
    Standard fields: phone_number, email, first_name, last_name, company_name, 
                     industry, location, pin_code, keywords
    Custom fields from metadata are flattened into columns.
    
    Args:
        contacts: List of contact dictionaries
    
    Returns:
        CSV content as string
    """
    if not contacts:
        return "phone_number,first_name,last_name,email,company_name,industry,location,pin_code,keywords\n"
    
    # Collect all standard field names
    standard_fields = ['phone_number', 'first_name', 'last_name', 'email', 
                       'company_name', 'industry', 'location', 'pin_code', 'keywords']
    
    # Collect all custom field keys from metadata across all contacts
    custom_fields = set()
    for contact in contacts:
        metadata = contact.get('metadata')
        if metadata and isinstance(metadata, dict):
            custom_fields.update(metadata.keys())
    
    # Sort custom fields for consistent column order
    custom_fields = sorted(custom_fields)
    
    # Combine standard and custom fields
    fieldnames = standard_fields + list(custom_fields)
    
    # Create StringIO buffer
    output = io.StringIO()
    
    # Write CSV
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    
    for contact in contacts:
        row = {}
        
        # Add standard fields
        for field in standard_fields:
            value = contact.get(field)
            if field == 'keywords' and isinstance(value, list):
                # Convert keywords array to comma-separated string
                row[field] = ', '.join(str(v) for v in value) if value else ''
            else:
                row[field] = str(value) if value is not None else ''
        
        # Add custom fields from metadata
        metadata = contact.get('metadata')
        if metadata and isinstance(metadata, dict):
            for field in custom_fields:
                value = metadata.get(field)
                if isinstance(value, (list, dict)):
                    # Convert complex types to JSON string
                    row[field] = json.dumps(value) if value else ''
                else:
                    row[field] = str(value) if value is not None else ''
        else:
            # Fill empty values for custom fields if no metadata
            for field in custom_fields:
                row[field] = ''
        
        writer.writerow(row)
    
    return output.getvalue()


def validate_bulk_contacts(contacts: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Validate a list of contacts, separating valid from invalid
    
    Args:
        contacts: List of contact dictionaries to validate
        
    Returns:
        Tuple of (valid_contacts, invalid_contacts_with_errors)
    """
    valid_contacts = []
    invalid_contacts = []
    
    for idx, contact in enumerate(contacts):
        try:
            validated = validate_contact_data(contact.copy())
            valid_contacts.append(validated)
        except ValueError as e:
            invalid_contacts.append({
                'index': idx,
                'contact': contact,
                'error': str(e)
            })
    
    return valid_contacts, invalid_contacts
