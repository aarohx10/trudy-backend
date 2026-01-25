"""
Contact Service
Business logic for contact management, validation, and CSV operations
"""
import re
import csv
import io
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


def parse_csv_contacts(file_content: str) -> List[Dict[str, Any]]:
    """
    Parse CSV file content into list of contact dictionaries
    
    Expected CSV columns:
    - phone_number (required)
    - first_name (optional)
    - last_name (optional)
    - email (optional)
    
    Args:
        file_content: CSV file content as string
        
    Returns:
        List of contact dictionaries
        
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
        required_columns = ['phone_number']
        
        # Check if required columns exist
        if not reader.fieldnames:
            raise ValueError("CSV file is empty or invalid")
        
        missing_columns = [col for col in required_columns if col not in reader.fieldnames]
        if missing_columns:
            raise ValueError(f"Missing required columns: {', '.join(missing_columns)}")
        
        # Parse rows
        for row_num, row in enumerate(reader, start=2):  # Start at 2 (header is row 1)
            try:
                # Clean up row data
                contact_data = {}
                for key, value in row.items():
                    if value and value.strip():
                        # Normalize column names (handle case variations)
                        normalized_key = key.strip().lower().replace(' ', '_')
                        contact_data[normalized_key] = value.strip()
                
                # Map common column name variations
                column_mapping = {
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
                }
                
                # Apply column mapping
                mapped_data = {}
                for key, value in contact_data.items():
                    mapped_key = column_mapping.get(key, key)
                    mapped_data[mapped_key] = value
                
                # Ensure phone_number exists
                if 'phone_number' not in mapped_data:
                    raise ValueError(f"Row {row_num}: phone_number is required")
                
                # Validate and normalize contact data
                validated_contact = validate_contact_data(mapped_data)
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
    Generate CSV content from list of contacts
    
    Args:
        contacts: List of contact dictionaries
        
    Returns:
        CSV content as string
    """
    if not contacts:
        return "phone_number,first_name,last_name,email\n"
    
    # Create StringIO buffer
    output = io.StringIO()
    
    # Define CSV columns
    fieldnames = ['phone_number', 'first_name', 'last_name', 'email']
    
    # Write CSV
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    
    for contact in contacts:
        # Extract only the fields we want in CSV
        row = {
            'phone_number': contact.get('phone_number', ''),
            'first_name': contact.get('first_name', ''),
            'last_name': contact.get('last_name', ''),
            'email': contact.get('email', ''),
        }
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
