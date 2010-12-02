@import <AppKit/CPTableView.j>

@implementation TableDataSource: CPObject
{
    JSONObject tbData;
    id _target;
}

- (void)initWithData:(CPData)data
{
    tbData = [data JSONObject];
}

- (void)setTarget:(id)target
{
    _target = target;    
}

- (CPInteger)numberOfRowsInTableView:(id)sender
{
    console.log(tbData);
    return [tbData count] + 1;
}

- (CPInteger)tableView:(id)aTableView objectValueForTableColumn:(id)tableColumn row:(id)aRow
{
    if (aRow == 0) {
        return "PROJECTS";    
    }
    return tbData[aRow - 1][[tableColumn identifier]];
}

- (BOOL)tableView:(CPTableView)aTableView isGroupRow:(int)aRow
{
    return aRow === 0;    
}

- (void)tableViewSelectionDidChange:(id)notification
{
    var selection = [[notification object] selectedRow];
    [_target loadProject:tbData[selection - 1]];
}

- (BOOL)tableView:(CPTableView)aTableView shouldSelectRow:(int)aRow
{
    if (aRow === 0) {
       return NO;
    }
    else {
        return YES;    
    }
}

/**
    var urlRequest = [CPURLRequest requestWithURL:"/ctrl/?action=transaction&type=in&desc=Test&value=10.0&tags=test1,test2"];
    [urlRequest setHTTPMethod:"GET"];
    var connection = [CPURLConnection connectionWithRequest:urlRequest delegate:self];
*/
@end

@implementation BuildsDataSource: CPObject
{
    JSONObject tbData;
    id tableView;
}

-(void)connection:(CPConnection)aConn didReceiveData:(CPString)data
{
    var cpdata = [[CPData alloc] initWithRawString:data];
    tbData = [cpdata JSONObject];
    [tableView reloadData];
}

- (CPInteger)numberOfRowsInTableView:(id)sender
{
    return [tbData count];
}

- (void)setTableView:(id)view
{
    tableView = view;    
}

- (CPInteger)tableView:(id)aTableView objectValueForTableColumn:(id)tableColumn row:(id)aRow
{
    console.log([tableColumn identifier]);
    console.log(aRow);
    return tbData[aRow][[tableColumn identifier]];
}

- (void)tableViewSelectionDidChange:(id)notification
{
    var selection = [[notification object] selectedRow];
}

- (BOOL)tableView:(CPTableView)aTableView shouldSelectRow:(int)aRow
{
        return YES;    
}

@end